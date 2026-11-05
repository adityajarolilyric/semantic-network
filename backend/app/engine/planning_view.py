"""Read-only pivot planning over generation-scoped materialised values."""

from dataclasses import dataclass, replace

from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q
from ..warehouse.repository import quote_literal as lit
from .contracts import ExecutionPlan, RelationSchema
from .measure_compiler import COMPLETE, VALUE, MeasureCompiler
from .relationships import Coordinate, LevelRelationshipAnalyzer
from .table_storage import GENERATION, column_sql, token

TARGET = "__planning_view"


def virtual_level(fields):
    return {
        "objectId": TARGET,
        "kind": "planningLevel",
        "name": "Planning View",
        "definition": {
            "grain": {"keyIds": [f["id"] for f in fields], "columns": []},
            "population": {"operation": "source", "sourceId": "__view"},
            "dimensionBindings": [],
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
        for dimension in self.dimensions.values():
            mappings = []
            for key in dimension["definition"]["keyColumnIds"]:
                matched = [
                    field
                    for field in fields
                    if ("dimension", dimension["objectId"], key)
                    in field["coordinate"].tokens
                ]
                if len(matched) != 1:
                    break
                mappings.append(
                    {"grainColumnId": matched[0]["id"], "dimensionColumnId": key}
                )
            else:
                if mappings:
                    bindings.append(
                        {
                            "dimensionId": dimension["objectId"],
                            "keyMappings": mappings,
                            "selectedAttributeColumnIds": [],
                        }
                    )
        self.levels[TARGET]["definition"]["dimensionBindings"] = bindings

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


class PlanningViewPlanner:
    def __init__(self, objects, base_id):
        self.objects = {o["objectId"]: o for o in objects}
        self.base_id = base_id

    def fields(self, request):
        result = []
        for index, ref in enumerate([*request.rows, *request.columns]):
            if ref.planningLevelId:
                level = self.objects.get(ref.planningLevelId)
                if not level or level["kind"] != "planningLevel":
                    raise MetadataError(
                        "invalid_view_field",
                        "A selected planning level no longer exists.",
                    )
                definition = level["definition"]
                columns = {
                    c["grainColumnId"]: c["name"]
                    for c in definition["grain"]["columns"]
                }
                columns.update(
                    {
                        c["columnId"]: c["name"]
                        for c in definition.get("populationAttributes", [])
                    }
                )
                if ref.columnId not in columns:
                    raise MetadataError(
                        "invalid_view_field",
                        "Choose a grain column or population attribute from the planning level.",
                    )
                coordinate = LevelRelationshipAnalyzer(
                    list(self.objects.values())
                ).outputs(level["objectId"])[ref.columnId]
                result.append(
                    {
                        "id": f"axis_{index}",
                        "planningLevelId": level["objectId"],
                        "columnId": ref.columnId,
                        "label": level["name"] + " · " + columns[ref.columnId],
                        "axis": "row" if index < len(request.rows) else "column",
                        "coordinate": coordinate,
                    }
                )
                continue
            dim = self.objects.get(ref.dimensionId)
            if not dim or dim["kind"] != "dimension":
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
                    "dimensionId": dim["objectId"],
                    "columnId": column["columnId"],
                    "label": dim["name"] + " · " + column["displayName"],
                    "axis": "row" if index < len(request.rows) else "column",
                    "coordinate": Coordinate(
                        frozenset(
                            {
                                ("dimension", dim["objectId"], column["columnId"]),
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
            from ..formula_language.expressions import contains_counts

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
                population["sourcePlanningLevelId"], seen | {level}
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
            from ..formula_language.expressions import walk, contains_counts

            expression = (d.get("formula") or {}).get("expression")
            if contains_counts(expression):
                count_source_levels.add(d["nativePlanningLevelId"])
            for node in walk(expression):
                if node["type"] == "measureRef":
                    collect_counts(node["measureId"])

        for mid in request.measures:
            collect_counts(mid)
        relationships = ViewRelationships(list(self.objects.values()), fields)
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
                    "The measure’s Output Planning Level must be built and determine every selected pivot attribute.",
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
                self.objects[mid]["definition"]["nativePlanningLevelId"] != lid
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
                and self.objects[mid]["definition"]["nativePlanningLevelId"] == lid
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
            raise MetadataError(
                "planning_view_unavailable",
                "No compatible materialised level can supply all selected attributes and measures. Check dimension bindings and materialise the required measures.",
                details=[
                    {"message": self.objects[lid]["name"] + ": " + reason}
                    for lid, reason in rejected.items()
                ],
            )
        chosen = min(
            candidates,
            key=lambda c: (
                not c.exact,
                populations[c.level_id]["rowCount"],
                len(relationships.levels[c.level_id]["definition"]["grain"]["keyIds"]),
                c.level_id,
            ),
        )
        chosen.explanation = (
            "Uses the measure's native planning level; no path to Base is defined."
            if chosen.native_only
            else "Uses the smallest compatible materialised population at the requested grain."
            if chosen.exact
            else "Uses a compatible coarser population whose configured aggregates can be combined safely."
            if chosen.level_id != self.base_id
            else "Uses Base to preserve the selected dimensions, measures and aggregation policies."
        )
        return chosen


class PlanningViewCompiler(MeasureCompiler):
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
                [self.view.level_id, *self.view.count_source_levels]
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
        stages = (
            ["nonTime", "time"]
            if policy.get("order", "nonTimeThenTime") == "nonTimeThenTime"
            else ["time", "nonTime"]
        )
        for stage in stages:
            if not pending[stage]:
                continue
            pending[stage] = False
            other = "time" if stage == "nonTime" else "nonTime"
            extra = (
                [
                    k
                    for k in self.keys(source)
                    if outputs[k].is_time == (other == "time")
                ]
                if pending[other]
                else []
            )
            groups = list(dict.fromkeys([*self.keys(TARGET), *extra]))
            value = self.aggregate(
                "aggregation_input." + q(VALUE),
                policy[stage],
                policy,
                q("__time_order") if order else None,
                "aggregation_input." + q(COMPLETE) if has_mixed else None,
            )
            from .value_states import mixed_group

            conflict = (
                mixed_group(
                    policy[stage],
                    "aggregation_input." + q(VALUE),
                    "aggregation_input." + q(COMPLETE),
                    q("__time_order") if order else None,
                )
                if has_mixed or policy[stage] == "single_value"
                else "0"
            )
            has_mixed = has_mixed or policy[stage] == "single_value"
            selected = [
                *(q(k) for k in groups),
                f"if(({conflict}) OR min(aggregation_input.{q(COMPLETE)})=0,NULL,{value}) AS {q(VALUE)}",
                f"if({conflict},toUInt8(2),min(aggregation_input.{q(COMPLETE)})) AS {q(COMPLETE)}",
            ]
            if order and stage == "nonTime" and pending["time"]:
                selected.append("min(__time_order) AS __time_order")
            data = (
                f"SELECT {', '.join(selected)} FROM ({data}) AS aggregation_input"
                + (" GROUP BY " + ", ".join(q(k) for k in groups) if groups else "")
            )
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
            raise MetadataError(
                "counting_input_unavailable",
                "A count argument is not materialised at the the measure’s Output Planning Level. Build the Semantic Network first.",
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
                evaluation_level = measure["definition"]["nativePlanningLevelId"]
                if (
                    self.relationships.analyze(evaluation_level, TARGET)["relationship"]
                    == "incompatible"
                ):
                    if self.relationships.analyze(
                        self.base_id, measure["definition"]["nativePlanningLevelId"]
                    )["relationship"] not in ("same", "equivalent", "roll_up"):
                        raise MetadataError(
                            "measure_path_unavailable",
                            "The calculation has no path to the requested grain.",
                        )
                result = self.compile_calculated_measure(measure, TARGET)
            else:
                query = self.stored_value(measure_id, self.view.level_id)
                result = self.aggregate_view(query, measure)
                coverage = self.coverage_nodes.get(result)
                native = measure["definition"]["nativePlanningLevelId"]
                if (
                    coverage
                    and native != self.view.level_id
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
        source = self.values[level]
        column = next(c for c in source["columns"] if c.get("measureId") == measure_id)
        keys = [q(source["keyColumns"][k]) + " AS " + q(k) for k in self.keys(level)]
        body = f"FROM {self.table(source['physicalName'])} WHERE {q(GENERATION)}={lit(token(source))}"
        coverage = source.get("coverageColumns", {}).get(measure_id)
        flag = q(coverage) if coverage else "toUInt8(1)"
        mixed = source.get("mixedColumns", {}).get(measure_id)
        if mixed:
            flag = f"if(coalesce({q(mixed)},0),toUInt8(2),{flag})"
        query = self.cte(
            f"SELECT {', '.join(keys)}, {column_sql(column)} AS {q(VALUE)}, {flag} AS {q(COMPLETE)} {body}"
        )
        if coverage:
            self.coverage_nodes[query] = True
        if mixed:
            self.mixed_nodes.add(query)
        return query

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
        from sqlglot import parse_one

        return ExecutionPlan(
            parse_one(sql, read="clickhouse"),
            tuple(self.assertions),
            RelationSchema(()),
            "planning-view-v1",
        )
