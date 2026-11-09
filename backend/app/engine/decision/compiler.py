"""Read-only pivot planning over generation-scoped materialised values."""

from sqlglot import parse_one

from ...core.errors import MetadataError
from ...warehouse.sql import quote_identifier as q
from ..measures.aggregation import AggregationOperator, AggregationPlan
from ..measures.compiler import MeasureCompiler
from ..measures.contracts import COMPLETE, VALUE
from ..sql.contracts import ExecutionPlan, RelationSchema
from .relationships import TARGET, ViewRelationships, virtual_level


class DecisionViewCompiler(MeasureCompiler):
    def __init__(self, database, objects, populations, managed, base_id, values, plan):
        super().__init__(
            database,
            [*objects, virtual_level(plan.fields)],
            populations,
            managed,
            base_id,
        )
        self.relationships = ViewRelationships(objects, plan.fields)
        self.levels[TARGET] = self.relationships.levels[TARGET]
        self.values, self.view = values, plan
        self.view_population = None

    def population(self, level):
        if level != TARGET:
            return super().population(level)
        if not self.view.fields:
            return "SELECT 1 AS __global"
        if self.view_population is None:
            projections = []
            for source in dict.fromkeys(
                [
                    self.view.level_id,
                    *self.view.measure_levels.values(),
                    *self.view.count_source_levels,
                ]
            ):
                seed = self.cte(
                    f"SELECT {', '.join(q(k) for k in self.keys(source))}, 0 AS {q(VALUE)} FROM ({self.population(source)})"
                )
                data = self.enrich(
                    seed,
                    source,
                    {
                        f["id"]: self.relationships.resolve(source, f["coordinate"])
                        for f in self.view.fields
                    },
                )
                projections.append(
                    f"SELECT {', '.join(q(f['id']) for f in self.view.fields)} FROM ({data})"
                )
            self.view_population = self.cte(
                "SELECT DISTINCT * FROM (" + " UNION ALL ".join(projections) + ")"
            )
        return "SELECT * FROM " + q(self.view_population)

    def join_keys(self, left, right, level):
        if level == TARGET:
            return (
                " AND ".join(
                    f"isNotDistinctFrom({left}.{q(k)}, {right}.{q(k)})"
                    for k in self.keys(level)
                )
                or "1"
            )
        return super().join_keys(left, right, level)

    def aggregate_view(self, query, measure, source=None):
        source = source or self.view.level_id
        coordinates = {
            f["id"]: self.relationships.resolve(source, f["coordinate"])
            for f in self.view.fields
        }
        outputs = self.relationships.outputs(source)
        removed = [
            outputs[k]
            for k in self.keys(source)
            if not self.relationships.resolve(TARGET, outputs[k])
        ]
        pending = {
            "time": any(c.is_time for c in removed),
            "nonTime": any(not c.is_time for c in removed),
        }
        if not any(pending.values()):
            selected = [*(q(k) for k in self.keys(TARGET)), q(VALUE), q(COMPLETE)]
            data = self.enrich(query, source, coordinates)
            result = self.cte(f"SELECT {', '.join(selected)} FROM ({data})")
            if query in self.coverage_nodes:
                self.coverage_nodes[result] = True
            if query in self.mixed_nodes:
                self.mixed_nodes.add(result)
            return result
        has_mixed = query in self.mixed_nodes
        policy = measure["definition"]["aggregation"]
        order = (
            self.ordering(source)
            if pending["time"] and policy["time"] in ("first", "last")
            else None
        )
        if order:
            coordinates["__time_order"] = order
        data = self.enrich(query, source, coordinates)
        aggregation = AggregationOperator.compile(
            self,
            data,
            AggregationPlan(
                tuple(self.keys(TARGET)),
                tuple(k for k in self.keys(source) if outputs[k].is_time),
                tuple(k for k in self.keys(source) if not outputs[k].is_time),
                pending["time"],
                pending["nonTime"],
                policy,
                bool(order),
            ),
            has_mixed,
        )
        data, has_mixed = aggregation.sql, aggregation.has_mixed
        selected = [*(q(k) for k in self.keys(TARGET)), q(VALUE), q(COMPLETE)]
        result = self.cte(f"SELECT {', '.join(selected)} FROM ({data})")
        if query in self.coverage_nodes:
            self.coverage_nodes[result] = True
        if has_mixed:
            self.mixed_nodes.add(result)
        return result

    def compile_measure_at_level(self, measure_id, target):
        if target != TARGET:
            key = (measure_id, target)
            if key in self.cache:
                return self.cache[key]
            if measure_id in self.values.get(target, {}).get("measureColumns", {}):
                result = self.stored_value(measure_id, target)
                self.cache[key] = result
                return result
            if key in self.visiting:
                raise MetadataError(
                    "measure_dependency_cycle",
                    "The selected formula contains a dependency cycle.",
                )
            if (
                self.measures[measure_id]["definition"].get("calculationType")
                == "AggregateFirst"
            ):
                self.visiting.add(key)
                try:
                    result = self.compile_calculated_measure(
                        self.measures[measure_id], target
                    )
                    self.cache[key] = result
                    return result
                finally:
                    self.visiting.remove(key)
            measure = self.measures[measure_id]
            native = measure["definition"]["nativeGrainId"]
            for source in dict.fromkeys([self.base_id, native]):
                if measure_id in self.values.get(source, {}).get(
                    "measureColumns", {}
                ) and self.relationships.analyze(source, target)["relationship"] in {
                    "same",
                    "equivalent",
                    "roll_up",
                }:
                    result = self.rollup(
                        self.stored_value(measure_id, source), measure, source, target
                    )
                    self.cache[key] = result
                    return result
            raise MetadataError(
                "counting_input_unavailable",
                "A count argument is not materialised at the the measure’s Output Dimension. Build the Semantic Network first.",
            )
        key = (measure_id, target)
        if key in self.cache:
            return self.cache[key]
        if key in self.visiting:
            raise MetadataError(
                "measure_dependency_cycle",
                "The selected formula contains a dependency cycle.",
            )
        self.visiting.add(key)
        try:
            measure = self.measures[measure_id]
            if measure_id not in self.view.leaves:
                # AggregateFirst inputs are resolved at the requested pivot grain.
                evaluation_level = measure["definition"]["nativeGrainId"]
                if (
                    self.relationships.analyze(evaluation_level, TARGET)["relationship"]
                    == "incompatible"
                ):
                    if self.relationships.analyze(
                        self.base_id, measure["definition"]["nativeGrainId"]
                    )["relationship"] not in ("same", "equivalent", "roll_up"):
                        raise MetadataError(
                            "measure_path_unavailable",
                            "The calculation has no path to the requested grain.",
                        )
                result = self.compile_calculated_measure(measure, TARGET)
            else:
                source_level = self.view.measure_levels.get(
                    measure_id, self.view.level_id
                )
                query = self.stored_value(measure_id, source_level)
                result = self.aggregate_view(query, measure, source_level)
                coverage = self.coverage_nodes.get(result)
                native = measure["definition"]["nativeGrainId"]
                if (
                    coverage
                    and native != source_level
                    and native in self.values
                    and self.relationships.analyze(native, TARGET)["relationship"]
                    in ("same", "equivalent", "roll_up")
                ):
                    fallback = self.aggregate_view(
                        self.stored_value(measure_id, native), measure, native
                    )
                    result = self.with_coverage(result, coverage, TARGET, fallback)
            self.cache[key] = result
            return result
        finally:
            self.visiting.remove(key)

    def stored_value(self, measure_id, level):
        return self.stored_measure(measure_id, level, self.values)

    def compile(self, measure_ids):
        joins, selected = [], ["p." + q(k) + " AS " + q(k) for k in self.keys(TARGET)]
        for index, mid in enumerate(measure_ids):
            node = self.compile_measure_at_level(mid, TARGET)
            alias = f"v{index}"
            joins.append(
                f"LEFT JOIN {q(node)} {alias} ON " + self.join_keys("p", alias, TARGET)
            )
            value, _ = self.cast_node_value(alias, node, self.measures[mid])
            selected.append(value + " AS " + q(f"value_{index}"))
        for index, mid in enumerate(measure_ids):
            selected.append(
                f"toUInt8(coalesce(v{index}.{q(COMPLETE)},1)=2) AS {q('__mixed_' + str(index))}"
            )
        sql = self.with_sql(
            f"SELECT {', '.join(selected)} FROM ({self.population(TARGET)}) p {' '.join(joins)}"
        )

        return ExecutionPlan(
            parse_one(sql, read="clickhouse"),
            tuple(self.assertions),
            RelationSchema(()),
            "decision-view-v1",
        )
