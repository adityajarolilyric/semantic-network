"""Read-only pivot planning over generation-scoped materialised values."""

from dataclasses import dataclass, field, replace

from sqlglot import parse_one

from ..core.errors import MetadataError
from ..domain.dimensions import dimension_columns
from ..domain.grains import transaction_column_reference
from ..formula_language.expressions import contains_counts, walk
from ..warehouse.sql import quote_identifier as q
from .compatibility import MeasureCompatibilityAnalyzer
from .contracts import ExecutionPlan, RelationSchema
from .grain_contexts import resolve_grain_contexts
from .aggregation import AggregationOperator, AggregationPlan
from .measure_compiler import COMPLETE, VALUE, MeasureCompiler
from .relationships import Coordinate, LevelRelationshipAnalyzer

TARGET = "__decision_view"


def virtual_level(fields):
    return {
        "objectId": TARGET,
        "kind": "dimension",
        "name": "Planning View",
        "definition": {
            "grain": {"keyIds": [f["id"] for f in fields], "columns": []},
            "population": {"operation": "source", "sourceId": "__view"},
            "memberBindings": [],
        },
    }


class ViewRelationships(LevelRelationshipAnalyzer):
    def __init__(self, objects, fields):
        super().__init__([*objects, virtual_level(fields)])
        self._cache[TARGET] = {
            f["id"]: replace(f["coordinate"], column_id=f["id"]) for f in fields
        }

        # A displayed dimension key can determine its attributes without making
        # those attributes part of the pivot grain.
        bindings = []
        for dimension in self.dimension_members.values():
            mappings = []
            for key in dimension["definition"]["keyColumnIds"]:
                matched = [
                    field
                    for field in fields
                    if ("dimensionMembers", dimension["objectId"], key)
                    in field["coordinate"].tokens
                ]
                if len(matched) != 1:
                    break
                mappings.append(
                    {"grainColumnId": matched[0]["id"], "memberColumnId": key}
                )
            else:
                if mappings:
                    bindings.append(
                        {
                            "memberCatalogId": dimension["objectId"],
                            "keyMappings": mappings,
                            "selectedAttributeColumnIds": [],
                        }
                    )
        self.levels[TARGET]["definition"]["memberBindings"] = bindings

    def resolve(self, source, target_coordinate):
        if source == TARGET:
            # Two displayed attributes can be proven aliases of the same source
            # field. Either grouping column proves that the source key is retained.
            matches = [
                c
                for c in self.outputs(TARGET).values()
                if c.tokens & target_coordinate.tokens
            ]
            if matches:
                return matches[0]
            return super().resolve(source, target_coordinate)
        return super().resolve(source, target_coordinate)


@dataclass
class ViewPlan:
    level_id: str
    exact: bool
    fields: list
    leaves: set
    explanation: str
    native_only: bool = False
    count_source_levels: tuple = ()
    measure_levels: dict = field(default_factory=dict)


class DecisionViewPlanner:
    def __init__(self, objects, base_id):
        self.graph = resolve_grain_contexts(objects)
        self.objects = dict(self.graph.by_id)
        self.relationships = LevelRelationshipAnalyzer(self.graph)
        self.base_id = base_id

    def fields(self, request):
        result = []
        for index, ref in enumerate([*request.rows, *request.columns]):
            if ref.dimensionId or ref.transactionId:
                grain_id, column_id = (ref.dimensionId, ref.columnId)
                owner_name = None
                if ref.transactionId:
                    grain_id, column_id = transaction_column_reference(
                        ref.transactionId, ref.columnId, list(self.objects.values())
                    )
                    owner_name = self.objects.get(ref.transactionId, {}).get("name")
                level = self.objects.get(grain_id)
                if not level or level["kind"] not in {"dimension", "grain"}:
                    raise MetadataError(
                        "invalid_view_field",
                        "The selected grain context no longer exists.",
                    )
                columns = {
                    c["columnId"]: c["name"]
                    for c in dimension_columns(level, list(self.objects.values()))
                }
                if column_id not in columns:
                    raise MetadataError(
                        "invalid_view_field",
                        "Choose a declared grain key or Dimension attribute.",
                    )
                coordinate = self.relationships.outputs(grain_id)[column_id]
                result.append(
                    {
                        "id": f"axis_{index}",
                        **ref.model_dump(exclude_none=True),
                        "label": (owner_name or level["name"])
                        + " · "
                        + columns[column_id],
                        "axis": "row" if index < len(request.rows) else "column",
                        "coordinate": coordinate,
                    }
                )
                continue
            dim = self.objects.get(ref.memberCatalogId)
            if not dim or dim["kind"] != "dimensionMembers":
                raise MetadataError(
                    "invalid_view_field", "A selected dimension no longer exists."
                )
            definition = dim["definition"]
            exposed = set(definition["keyColumnIds"]) | set(
                definition["attributeColumnIds"]
            )
            column = next(
                (
                    c
                    for c in definition["columns"]
                    if c["columnId"] == ref.columnId and c["columnId"] in exposed
                ),
                None,
            )
            if not column:
                raise MetadataError(
                    "invalid_view_field",
                    "Choose an exposed attribute from the dimension.",
                )
            result.append(
                {
                    "id": f"axis_{index}",
                    "memberCatalogId": dim["objectId"],
                    "columnId": column["columnId"],
                    "label": dim["name"] + " · " + column["displayName"],
                    "axis": "row" if index < len(request.rows) else "column",
                    "coordinate": Coordinate(
                        frozenset(
                            {
                                (
                                    "dimensionMembers",
                                    dim["objectId"],
                                    column["columnId"],
                                ),
                                (
                                    "source",
                                    definition["sourceId"],
                                    column["sourceColumnId"],
                                ),
                            }
                        ),
                        is_time=dim["isTimeDim"],
                    ),
                }
            )
        return result

    def leaves(self, measures, exact, artifact):
        leaves, visiting = set(), set()

        def visit(mid):
            obj = self.objects.get(mid)
            if not obj or obj["kind"] != "measure":
                raise MetadataError(
                    "invalid_view_measure", "A selected measure no longer exists."
                )
            if mid in visiting:
                raise MetadataError(
                    "measure_dependency_cycle",
                    "The selected formula has a dependency cycle.",
                )
            d = obj["definition"]

            counted = contains_counts((d.get("formula") or {}).get("expression"))
            if d.get("calculationType") != "AggregateFirst" or (
                exact and mid in artifact.get("measureColumns", {}) and not counted
            ):
                leaves.add(mid)
                return
            visiting.add(mid)

            def expression(node):
                if node["type"] == "measureRef":
                    visit(node["measureId"])
                elif node["type"] == "binary":
                    expression(node["left"])
                    expression(node["right"])
                elif node["type"] == "call":
                    for child in node["arguments"]:
                        expression(child)

            expression(d["formula"]["expression"])
            visiting.remove(mid)

        for mid in measures:
            visit(mid)
        return leaves

    def complete_projection(self, level, seen=None):
        if level == self.base_id:
            return True
        seen = set() if seen is None else seen
        if level in seen:
            return False
        definition = self.objects[level]["definition"]
        population = definition["population"]
        return (
            population["operation"] == "distinct_projection"
            and not definition.get("filters")
            and self.complete_projection(
                population["sourceDimensionId"], seen | {level}
            )
        )

    def plan(self, request, populations, values):
        fields = self.fields(request)
        count_source_levels = set()
        visited = set()

        def collect_counts(mid):
            if mid in visited:
                return
            visited.add(mid)
            obj = self.objects.get(mid)
            if not obj or obj["kind"] != "measure":
                return
            d = obj["definition"]

            expression = (d.get("formula") or {}).get("expression")
            if contains_counts(expression):
                count_source_levels.add(d["nativeGrainId"])
            for node in walk(expression):
                if node["type"] == "measureRef":
                    collect_counts(node["measureId"])

        for mid in request.measures:
            collect_counts(mid)
        relationships = ViewRelationships(self.graph, fields)

        availability = MeasureCompatibilityAnalyzer(
            list(self.objects.values()), self.base_id, relationships
        )
        for mid in request.measures:
            result = availability.outcome(mid, TARGET)
            if not result.available:
                raise MetadataError("decision_view_unavailable", result.reason)
        for counting in count_source_levels:
            if (
                counting not in populations
                or counting not in values
                or any(
                    not relationships.resolve(counting, f["coordinate"]) for f in fields
                )
            ):
                raise MetadataError(
                    "counting_view_unavailable",
                    "The measure’s Output Dimension must be built and determine every selected pivot attribute.",
                )
        base_outputs = relationships.outputs(self.base_id)
        removed = [
            c
            for c in base_outputs.values()
            if c.column_id
            in relationships.levels[self.base_id]["definition"]["grain"]["keyIds"]
            and not relationships.resolve(TARGET, c)
        ]
        candidates, rejected = [], {}
        for lid, artifact in values.items():
            if lid not in populations:
                continue
            if any(not relationships.resolve(lid, f["coordinate"]) for f in fields):
                rejected[lid] = "Required dimension attributes are unavailable."
                continue
            connected = relationships.analyze(self.base_id, lid)["relationship"] in (
                "same",
                "equivalent",
                "roll_up",
            )
            keys = relationships.levels[lid]["definition"]["grain"]["keyIds"]
            exact = all(
                relationships.resolve(TARGET, relationships.outputs(lid)[k])
                for k in keys
            )
            leaves = self.leaves(request.measures, exact, artifact)
            if not connected and any(
                self.objects[mid]["definition"]["nativeGrainId"] != lid
                for mid in leaves
            ):
                rejected[lid] = (
                    "An independent level can supply only its own native measures and their supported formulas."
                )
                continue
            if not leaves <= artifact.get("measureColumns", {}).keys():
                rejected[lid] = (
                    "A required measure or formula input is not materialised here."
                )
                continue
            typed_native = bool(leaves) and all(
                self.objects[mid]["definition"]["resultType"]["type"]
                in {"string", "date", "boolean"}
                and self.objects[mid]["definition"]["nativeGrainId"] == lid
                for mid in leaves
            )
            if (
                connected
                and lid != self.base_id
                and not typed_native
                and not self.complete_projection(lid)
            ):
                rejected[lid] = (
                    "Population coverage is not proven to be a complete projection of Base."
                )
                continue
            if connected and lid != self.base_id and not exact and not typed_native:
                safe = True
                for mid in leaves:
                    policy = self.objects[mid]["definition"]["aggregation"]
                    methods = {
                        policy["time"] if c.is_time else policy["nonTime"]
                        for c in removed
                    }
                    if len(methods) > 1 or not methods <= {
                        "sum",
                        "min",
                        "max",
                        "single_value",
                        "any_true",
                        "all_true",
                    }:
                        safe = False
                    if (
                        policy["nullInputPolicy"] == "ignore"
                        and policy["allNullResult"] == "zero"
                        and methods & {"min", "max"}
                    ):
                        safe = False
                if not safe:
                    rejected[lid] = (
                        "Re-aggregating stored values could change the configured result; use Base."
                    )
                    continue
            candidates.append(
                ViewPlan(
                    lid,
                    exact,
                    fields,
                    leaves,
                    "",
                    native_only=not connected,
                    count_source_levels=tuple(sorted(count_source_levels)),
                )
            )
        if not candidates:
            # Inputs can have different native storage grains. Aggregate each
            # independently before joining at the requested axes, never raw facts.
            leaves = self.leaves(request.measures, False, {})
            measure_levels = {}
            for mid in leaves:
                definition = self.objects[mid]["definition"]
                native = definition["nativeGrainId"]
                sources = [self.base_id, native]
                if definition["resultType"]["type"] in {"string", "date", "boolean"}:
                    sources.reverse()
                source = next(
                    (
                        lid
                        for lid in sources
                        if lid in values
                        and lid in populations
                        and mid in values[lid].get("measureColumns", {})
                        and relationships.analyze(lid, TARGET)["relationship"]
                        in {"same", "equivalent", "roll_up"}
                        and all(
                            relationships.resolve(lid, f["coordinate"]) for f in fields
                        )
                    ),
                    None,
                )
                if source is None:
                    raise MetadataError(
                        "decision_view_unavailable",
                        f'"{self.objects[mid]["name"]}" has no stored native or Base representation supporting the selected attributes.',
                    )
                measure_levels[mid] = source
            sources = list(
                dict.fromkeys([*measure_levels.values(), *count_source_levels])
            )
            if not sources:
                sources = [
                    lid
                    for lid in values
                    if all(relationships.resolve(lid, f["coordinate"]) for f in fields)
                ]
            if not sources:
                raise MetadataError(
                    "decision_view_unavailable",
                    "No stored native or Base population supports the selected attributes.",
                )
            return ViewPlan(
                sources[0],
                False,
                fields,
                leaves,
                "Aggregates native or Base representations independently at the requested grain.",
                native_only=self.base_id not in sources,
                count_source_levels=tuple(sorted(count_source_levels)),
                measure_levels=measure_levels,
            )
        chosen = min(
            candidates,
            key=lambda c: (
                not c.exact,
                populations[c.level_id].get("rowCount")
                if populations[c.level_id].get("rowCount") is not None
                else values[c.level_id]["rowCount"],
                len(relationships.levels[c.level_id]["definition"]["grain"]["keyIds"]),
                c.level_id,
            ),
        )
        chosen.explanation = (
            "Uses the measure's native dimension; no path to Base is defined."
            if chosen.native_only
            else "Uses the smallest compatible materialised population at the requested grain."
            if chosen.exact
            else "Uses a compatible coarser population whose configured aggregates can be combined safely."
            if chosen.level_id != self.base_id
            else "Uses Base to preserve the selected dimension_members, measures and aggregation policies."
        )
        return chosen


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
