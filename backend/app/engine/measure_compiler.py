from dataclasses import dataclass

from sqlglot import parse_one

from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q
from ..warehouse.repository import quote_literal as lit
from .compilation_context import CompilationContext
from .contracts import Check, ExecutionPlan, RelationSchema
from .filters import dimension_filter_columns
from .planning_compiler import PlanningLevelCompiler
from .relationships import Coordinate, LevelRelationshipAnalyzer
from .table_storage import GENERATION, column_sql, token

VALUE = "__value"
COMPLETE = "__complete"
LEGACY_MEASURE_COMPILER_VERSION = (
    "measure-v4-aggregation-derived-round6-sqlglot-27.28.1"
)
TYPED_MEASURE_COMPILER_VERSION = "measure-v5-typed-formulas-native-only"
MEASURE_COMPILER_VERSION = "measure-v6-planning-language-counts"
MATERIALISATION_POLICY = "aggregation-derived-disaggregation-v1-round6"


@dataclass(frozen=True)
class MeasureNode:
    measure_id: str
    level_id: str
    operation: str


class MeasureCompiler(PlanningLevelCompiler):
    """Compile a measure dependency DAG into ClickHouse CTEs; no client SQL."""

    def __init__(
        self,
        database,
        objects,
        populations,
        managed,
        base_id,
        previous=None,
        resolution_profiles=None,
        resolution_max_rows=1000000,
    ):
        super().__init__(database)
        self.context = CompilationContext(
            database, objects, managed, resolution_max_rows=resolution_max_rows
        )
        self.objects = self.context.objects
        self.input_checks = self.context.input_checks
        self.measures = {
            k: v for k, v in self.objects.items() if v["kind"] == "measure"
        }
        self.levels = {
            k: v for k, v in self.objects.items() if v["kind"] == "planningLevel"
        }
        self.relationships = LevelRelationshipAnalyzer(objects)
        self.populations, self.managed, self.base_id = populations, managed, base_id
        self.previous = previous
        self.resolution_profiles = resolution_profiles or {}
        self.resolution_max_rows = resolution_max_rows
        self.transaction_relations = {}
        self.object_inputs = self.context.object_inputs
        self.ctes = self.context.ctes
        self.cache, self.visiting, self.nodes = {}, set(), []
        self.coverage_nodes = {}
        self.mixed_nodes = set()
        self.assertions = self.input_checks
        self.max_nodes = 1024
        self._lookup_checks = set()

    def object_input(self, owner, relation):
        self.context.max_nodes = self.max_nodes
        return self.context.object_input(owner, relation)

    def stored_measure(self, mid, level, snapshots):
        artifact = snapshots.get(level, {})
        if mid not in artifact.get("measureColumns", {}):
            raise MetadataError(
                "measure_path_unavailable",
                "This measure was not materialised at this level in the captured generation.",
            )
        column = next(c for c in artifact["columns"] if c.get("measureId") == mid)
        selected = [
            q(artifact["keyColumns"][k]) + " AS " + q(k) for k in self.keys(level)
        ]
        selected.append(column_sql(column) + " AS " + q(VALUE))
        body = f"FROM {self.table(artifact['physicalName'])} WHERE {q(GENERATION)}={lit(token(artifact))}"
        coverage = artifact.get("coverageColumns", {}).get(mid)
        flag = q(coverage) if coverage else "toUInt8(1)"
        mixed_column = artifact.get("mixedColumns", {}).get(mid)
        if mixed_column:
            flag = f"if(coalesce({q(mixed_column)},0),toUInt8(2),{flag})"
        selected.append(flag + " AS " + q(COMPLETE))
        result = self.cte(f"SELECT {', '.join(selected)} {body}")
        if coverage:
            self.coverage_nodes[result] = True
        if mixed_column:
            self.mixed_nodes.add(result)
        return result

    def keys(self, level):
        return self.levels[level]["definition"]["grain"]["keyIds"]

    def population(self, level):
        artifact = self.populations[level]
        return f"SELECT {', '.join(column_sql(c) + ' AS ' + q(c['columnId']) for c in artifact['columns'])} FROM {self.table(artifact['physicalName'])} WHERE {q(GENERATION)}={lit(token(artifact))}"

    def cte(self, sql):
        self.context.max_nodes = self.max_nodes
        return self.context.cte(sql)

    def with_sql(self, sql):
        return self.context.with_sql(sql)

    def join_keys(self, left, right, level):
        return " AND ".join(f"{left}.{q(k)} = {right}.{q(k)}" for k in self.keys(level))

    def source_columns(self, source):
        return {c["columnId"]: c for c in self.managed[source]["columns"]}

    def widen(self, expression, measure):
        dtype = measure["definition"]["resultType"]
        if dtype["type"] in {"string", "date", "boolean"}:
            return expression
        if dtype["type"] == "integer":
            return f"toInt256({expression})"
        if dtype["type"] == "decimal":
            return f"toDecimal256({expression}, {dtype['scale']})"
        return f"toFloat64({expression})"

    def divide(self, left, right, measure):
        if measure["definition"]["resultType"]["type"] != "decimal":
            return f"({left}) / nullIf(({right}),0)"
        scale = min(60, max(18, measure["definition"]["resultType"].get("scale") or 0))
        return f"divideDecimal(toDecimal256({left},{scale}), nullIf(toDecimal256({right},{scale}),0), {scale})"

    def aggregate(self, expression, method, policy, ordering=None, state=None):
        if method in ("first", "last"):
            if not ordering:
                raise MetadataError(
                    "time_order_required",
                    "First/last aggregation requires an explicit Time ordering mapping.",
                )
            function = "argMinIf" if method == "first" else "argMaxIf"
            eligible = f"isNotNull({expression})" + (
                f" OR ({state})=2" if state else ""
            )
            inner = f"tupleElement({function}(tuple({expression}), {ordering}, {eligible}),1)"
        elif method == "single_value":
            inner = f"singleValueOrNull({expression})"
        elif method in {"any_true", "all_true"}:
            inner = f"{'max' if method == 'any_true' else 'min'}({expression})"
        elif method == "count_distinct":
            inner = f"uniqExact({expression})"
        elif method == "count":
            inner = f"count({expression})"
        else:
            inner = f"{method}({expression})"
        empty = "0" if policy["allNullResult"] == "zero" else "NULL"
        eligible_count = f"isNotNull({expression})" + (
            f" OR ({state})=2" if state and method in {"first", "last"} else ""
        )
        result = f"if(countIf({eligible_count})=0,{empty},{inner})"
        if policy["nullInputPolicy"] == "propagate":
            null = f"isNull({expression})" + (f" AND ({state})!=2" if state else "")
            result = f"if(countIf({null})>0,NULL,{result})"
        return result

    def resolve_transaction(self, transaction_id):
        from .transaction_resolution import TransactionResolutionCompiler

        if transaction_id not in self.transaction_relations:
            tx = self.objects[transaction_id]
            if tx["definition"].get("mappingMode") != "resolve_multi_grain":
                from .calendar_resolution import CalendarResolutionCompiler
                from .transaction_resolution import ResolvedTransaction

                if tx["definition"].get("calendarResolution"):
                    c = CalendarResolutionCompiler(self, tx)
                    node = c.compile()
                    input_cols = c.p.schema()
                else:
                    table, input_cols = self.object_input(
                        tx, self.managed[tx["definition"]["sourceId"]]
                    )
                    node = self.cte(
                        "SELECT "
                        + ", ".join(
                            q(c["name"]) + " AS " + q(c["columnId"]) for c in input_cols
                        )
                        + " FROM "
                        + table
                    )
                cols = [{**col, "storageName": col["columnId"]} for col in input_cols]
                result = ResolvedTransaction(
                    node, cols, tx["definition"]["keyMappings"], node, [node], node
                )
                self.transaction_relations[transaction_id] = self.filter_transaction(
                    tx, result
                )
                return self.transaction_relations[transaction_id]
            if transaction_id not in self.resolution_profiles:
                raise MetadataError(
                    "resolution_not_prepared",
                    "Prepare the transaction matching profiles before compiling measures.",
                )
            result = TransactionResolutionCompiler(
                self,
                tx,
                self.resolution_profiles[transaction_id],
                self.resolution_max_rows,
            ).compile()
            self.transaction_relations[transaction_id] = self.filter_transaction(
                tx, result
            )
        return self.transaction_relations[transaction_id]

    def filter_transaction(self, transaction, resolved):
        from dataclasses import replace

        from .filters import FilterCompiler

        filters = transaction["definition"].get("filters")
        if not filters:
            return resolved
        sql = FilterCompiler.apply(
            "SELECT * FROM " + q(resolved.node), filters, resolved.columns
        )
        return replace(resolved, node=self.cte(sql), unfiltered_node=resolved.node)

    def compile_native_measure(self, measure):
        d = measure["definition"]
        level = d["nativePlanningLevelId"]
        tx = self.objects[d["transactionId"]]["definition"]
        if tx.get("mappingMode") == "resolve_multi_grain" or (
            tx.get("calendarResolution") or tx.get("pivot") or tx.get("filters")
        ):
            resolved = self.resolve_transaction(d["transactionId"])
            table = q(resolved.node)
            columns = {
                c["columnId"]: {**c, "name": c["storageName"]} for c in resolved.columns
            }
            mappings = resolved.mappings
        else:
            source = self.managed[tx["sourceId"]]
            table = self.table(source["physicalName"])
            columns = self.source_columns(tx["sourceId"])
            mappings = tx["keyMappings"]
        value = q(columns[d["valueColumnId"]]["name"])
        group = ", ".join(q(columns[m["sourceColumnId"]]["name"]) for m in mappings)
        selected = ", ".join(
            q(columns[m["sourceColumnId"]]["name"]) + " AS " + q(m["grainColumnId"])
            for m in mappings
        )
        agg = self.aggregate(
            self.widen(value, measure), d["aggregation"]["nonTime"], d["aggregation"]
        )
        typed = d["resultType"]["type"] in {"string", "date", "boolean"}
        mixed = (
            f"toUInt8(uniqExact({self.widen(value, measure)})>1)"
            if typed and d["aggregation"]["nonTime"] == "single_value"
            else "toUInt8(0)"
        )
        grouped = None
        if tx.get("mappingMode") == "resolve_multi_grain" or (
            tx.get("calendarResolution") or tx.get("pivot") or tx.get("filters")
        ):
            from .date_range_resolution import RangeMeasureAggregator

            grouped = RangeMeasureAggregator(
                self, self.objects[d["transactionId"]], resolved, measure
            ).compile()
        if grouped is None:
            grouped = self.cte(
                f"SELECT {selected}, {agg} AS {q(VALUE)}, {mixed} AS __mixed, toUInt8(1) AS __present FROM {table} GROUP BY {group}"
            )
        self.assertions.append(
            Check(
                "unmatched_transaction_grain",
                f"SELECT a.* FROM {q(grouped)} a LEFT JOIN (SELECT *,toUInt8(1) AS __matched FROM ({self.population(level)})) p ON {self.join_keys('a', 'p', level)} WHERE coalesce(p.__matched,0)=0 LIMIT 20",
            )
        )
        absent = "0" if d["missingTransactionValue"] == "zero" else "NULL"
        result = self.cte(
            f"SELECT {', '.join('p.' + q(k) + ' AS ' + q(k) for k in self.keys(level))}, if(coalesce(a.__present,0)=0,{absent},a.{q(VALUE)}) AS {q(VALUE)}{', if(coalesce(a.__mixed,0),toUInt8(2),toUInt8(1)) AS ' + q(COMPLETE) if typed else ''} FROM ({self.population(level)}) p LEFT JOIN {q(grouped)} a ON {self.join_keys('p', 'a', level)}"
        )

        if typed:
            self.mixed_nodes.add(result)
        return result

    def enrich(self, query, level, coordinates):
        """Map logical coordinates without requiring dimension attributes to be exposed."""
        selected = ["r." + q(k) + " AS " + q(k) for k in self.keys(level)] + [
            "r." + q(VALUE) + " AS " + q(VALUE),
            (
                "r." + q(COMPLETE)
                if query in self.coverage_nodes or query in self.mixed_nodes
                else "toUInt8(1)"
            )
            + " AS "
            + q(COMPLETE),
        ]
        joins = []
        dimensions = {}
        for alias, coordinate in coordinates.items():
            if coordinate is None:
                raise MetadataError(
                    "measure_path_unavailable",
                    "A required key/attribute mapping is unavailable.",
                )
            if coordinate.column_id:
                expression = "p." + q(coordinate.column_id)
            else:
                dim_id = coordinate.dimension_id
                dim = self.objects[dim_id]
                definition = dim["definition"]
                if dim_id not in dimensions:
                    binding = next(
                        b
                        for b in self.levels[level]["definition"]["dimensionBindings"]
                        if b["dimensionId"] == dim_id
                    )
                    source = self.managed[definition["sourceId"]]
                    input_table, input_cols = self.object_input(dim, source)
                    columns = {c["columnId"]: c for c in input_cols}
                    alias_dim = "lookup_" + str(len(dimensions))
                    dimensions[dim_id] = alias_dim
                    dim_sql = (
                        "SELECT "
                        + ", ".join(
                            q(columns[c["sourceColumnId"]]["name"])
                            + " AS "
                            + q(c["columnId"])
                            for c in definition["columns"]
                        )
                        + " FROM "
                        + input_table
                    )
                    if definition.get("filters"):
                        dim_sql = f"SELECT * FROM ({dim_sql}) WHERE {self.filter_sql(definition['filters'], dimension_filter_columns(definition, input_cols))}"
                    dim_sql = self.reduced_lookup(
                        dim_sql,
                        [m["dimensionColumnId"] for m in binding["keyMappings"]],
                        [
                            c.dimension_column_id
                            for c in coordinates.values()
                            if c and c.dimension_id == dim_id
                        ],
                    )
                    on = " AND ".join(
                        "p."
                        + q(m.get("grainColumnId") or m.get("populationColumnId"))
                        + " = "
                        + alias_dim
                        + "."
                        + q(m["dimensionColumnId"])
                        for m in binding["keyMappings"]
                    )
                    joins.append(
                        f"LEFT JOIN (SELECT *,toUInt8(1) AS __coordinate_match FROM ({dim_sql})) {alias_dim} ON {on}"
                    )
                    selected.append(
                        f"throwIf(coalesce({alias_dim}.__coordinate_match,0)=0,'SEMANTIC_UNMATCHED_DIMENSION') AS {q('__coordinate_guard_' + alias_dim)}"
                    )
                    marker = (level, dim_id)
                    if marker not in self._lookup_checks:
                        self._lookup_checks.add(marker)
                        keys = ", ".join(
                            q(m["dimensionColumnId"]) for m in binding["keyMappings"]
                        )
                        self.assertions.append(
                            Check(
                                "ambiguous_dimension_lookup",
                                f"SELECT {keys},count() FROM ({dim_sql}) GROUP BY {keys} HAVING count()>1 LIMIT 20",
                            )
                        )
                expression = (
                    dimensions[dim_id] + "." + q(coordinate.dimension_column_id)
                )
            selected.append(expression + " AS " + q(alias))
        # Force lookup match guards even when an outer aggregate prunes unused columns.
        guards = [
            f"throwIf(coalesce({a}.__coordinate_match,0)=0,'SEMANTIC_UNMATCHED_DIMENSION')=0"
            for a in dimensions.values()
        ]
        return (
            f"SELECT {', '.join(selected)} FROM {q(query)} r INNER JOIN ({self.population(level)}) p ON {self.join_keys('r', 'p', level)} {' '.join(joins)}"
            + (" WHERE " + " AND ".join(guards) if guards else "")
        )

    def ordering(self, level):
        for dim in self.relationships.dimensions.values():
            if not dim["isTimeDim"]:
                continue
            cid = dim["definition"]["time"]["orderingColumnId"]
            col = next(c for c in dim["definition"]["columns"] if c["columnId"] == cid)
            symbol = Coordinate(
                frozenset(
                    {
                        ("dimension", dim["objectId"], cid),
                        (
                            "source",
                            dim["definition"]["sourceId"],
                            col["sourceColumnId"],
                        ),
                    }
                ),
                is_time=True,
            )
            found = self.relationships.resolve(level, symbol)
            if found:
                return found
        return None

    def rollup(self, query, measure, source, target):
        mapping = self.relationships.mapping(source, target)
        coordinates = {"__target_" + k: v for k, v in mapping.items()}
        source_outputs = self.relationships.outputs(source)
        removed = [
            k
            for k in self.keys(source)
            if self.relationships.resolve(target, source_outputs[k]) is None
        ]
        time_removed = any(source_outputs[k].is_time for k in removed)
        non_time_removed = any(not source_outputs[k].is_time for k in removed)
        policy = measure["definition"]["aggregation"]
        has_mixed = query in self.mixed_nodes
        order_symbol = (
            self.ordering(source)
            if time_removed and policy["time"] in ("first", "last")
            else None
        )
        if order_symbol:
            coordinates["__time_order"] = order_symbol
        data = self.enrich(query, source, coordinates)
        target_ids = ["__target_" + k for k in self.keys(target)]
        time_ids = [k for k in self.keys(source) if source_outputs[k].is_time]
        non_time_ids = [k for k in self.keys(source) if not source_outputs[k].is_time]
        stages = (
            ["nonTime", "time"]
            if policy.get("order", "nonTimeThenTime") == "nonTimeThenTime"
            else ["time", "nonTime"]
        )
        pending = {"nonTime": non_time_removed, "time": time_removed}
        for stage in stages:
            if not pending[stage]:
                continue
            pending[stage] = False
            extra = (
                time_ids
                if stage == "nonTime" and pending["time"]
                else non_time_ids
                if stage == "time" and pending["nonTime"]
                else []
            )
            groups = list(dict.fromkeys([*target_ids, *extra]))
            value = self.aggregate(
                "aggregation_input." + q(VALUE),
                policy[stage],
                policy,
                q("__time_order") if order_symbol else None,
                "aggregation_input." + q(COMPLETE) if has_mixed else None,
            )
            order_extra = (
                ", min(__time_order) AS __time_order"
                if order_symbol and stage == "nonTime" and pending["time"]
                else ""
            )
            from .value_states import mixed_group

            conflict = (
                mixed_group(
                    policy[stage],
                    "aggregation_input." + q(VALUE),
                    "aggregation_input." + q(COMPLETE),
                    q("__time_order") if order_symbol else None,
                )
                if has_mixed or policy[stage] == "single_value"
                else "0"
            )
            has_mixed = has_mixed or policy[stage] == "single_value"
            flag = f"if({conflict},toUInt8(2),min(aggregation_input.{q(COMPLETE)}))"
            data = f"SELECT {', '.join(q(k) for k in groups)}, if(({conflict}) OR min(aggregation_input.{q(COMPLETE)})=0,NULL,{value}) AS {q(VALUE)}, {flag} AS {q(COMPLETE)}{order_extra} FROM ({data}) AS aggregation_input GROUP BY {', '.join(q(k) for k in groups)}"

        mapped = self.cte(
            f"SELECT {', '.join(q('__target_' + k) + ' AS ' + q(k) for k in self.keys(target))}, {q(VALUE)}, {q(COMPLETE)} FROM ({data})"
        )
        if query in self.coverage_nodes:
            self.coverage_nodes[mapped] = True
        if has_mixed:
            self.mixed_nodes.add(mapped)
        return self.align(mapped, target)

    def align(self, query, level):
        flag = (
            f"coalesce(r.{q(COMPLETE)},1)"
            if query in self.coverage_nodes or query in self.mixed_nodes
            else "toUInt8(1)"
        )
        result = self.cte(
            f"SELECT {', '.join('p.' + q(k) + ' AS ' + q(k) for k in self.keys(level))}, r.{q(VALUE)} AS {q(VALUE)}, {flag} AS {q(COMPLETE)} FROM ({self.population(level)}) p LEFT JOIN {q(query)} r ON {self.join_keys('p', 'r', level)}"
        )
        if query in self.coverage_nodes:
            self.coverage_nodes[result] = True
        if query in self.mixed_nodes:
            self.mixed_nodes.add(result)
        return result

    def disaggregate(self, parent, measure, source, target):
        """Initialise finer values from aggregation, never from edit allocation."""
        mapping = self.relationships.mapping(target, source)
        child = self.cte(
            "SELECT "
            + ", ".join(q(k) for k in self.keys(target))
            + f", NULL AS {q(VALUE)} FROM ({self.population(target)})"
        )
        child = self.cte(
            self.enrich(child, target, {"__parent_" + k: c for k, c in mapping.items()})
        )
        parent_ids = ["__parent_" + k for k in self.keys(source)]
        on = " AND ".join(
            "c." + q("__parent_" + k) + " = p." + q(k) for k in self.keys(source)
        )
        grouped = ", ".join(q(k) for k in parent_ids)
        self.assertions.append(
            Check(
                "no_disaggregation_children",
                f"SELECT p.* FROM {q(parent)} p LEFT JOIN (SELECT {grouped},toUInt8(1) AS __matched FROM {q(child)} GROUP BY {grouped}) c ON {on} WHERE coalesce(c.__matched,0)=0 AND isNotNull(p.{q(VALUE)}) LIMIT 20",
            )
        )
        outputs = self.relationships.outputs(target)
        expansion_axes = self.relationships.expansion_axes(source, target)
        policy = measure["definition"]["aggregation"]
        aggregation_order = (
            ["nonTime", "time"]
            if policy.get("order", "nonTimeThenTime") == "nonTimeThenTime"
            else ["time", "nonTime"]
        )
        expanded = []
        value = "p." + q(VALUE)
        for axis in reversed(aggregation_order):
            if axis not in expansion_axes:
                continue
            axis_keys = [
                k for k in self.keys(target) if outputs[k].is_time == (axis == "time")
            ]
            if policy[axis] == "sum":
                # Distinct intermediate combinations avoid counting one day once
                # per location, or one location once per day. They reflect the
                # real (possibly irregular) target population, not a Cartesian size.
                partition = ", ".join("c." + q(k) for k in [*parent_ids, *expanded])
                members = ", ".join("c." + q(k) for k in axis_keys)
                count = f"uniqExact(tuple({members})) OVER (PARTITION BY {partition})"
                value = self.divide(value, count, measure)
            expanded.extend(axis_keys)
        flag = (
            f"coalesce(p.{q(COMPLETE)},0)"
            if parent in self.coverage_nodes
            else "toUInt8(1)"
        )
        keys = ", ".join("c." + q(k) + " AS " + q(k) for k in self.keys(target))
        result = self.cte(
            f"SELECT {keys}, {value} AS {q(VALUE)}, {flag} AS {q(COMPLETE)} FROM {q(child)} c LEFT JOIN {q(parent)} p ON {on}"
        )
        if parent in self.coverage_nodes:
            self.coverage_nodes[result] = True
        return result

    def with_coverage(self, query, coverage, level, fallback=None):
        """Recover incomplete groups from a proven native representation."""
        if not fallback:
            return query
        keys = ", ".join("r." + q(k) + " AS " + q(k) for k in self.keys(level))
        keys = keys + ", " if keys else ""
        complete = f"r.{q(COMPLETE)}"
        fc = f"coalesce(f.{q(COMPLETE)},0)" if fallback in self.coverage_nodes else "1"
        value = f"if({complete},r.{q(VALUE)},if({fc},f.{q(VALUE)},NULL))"
        result = self.cte(
            f"SELECT {keys}{value} AS {q(VALUE)}, toUInt8({complete} OR ({fc})) AS {q(COMPLETE)} FROM {q(query)} r LEFT JOIN {q(fallback)} f ON {self.join_keys('r', 'f', level)}"
        )
        self.coverage_nodes[result] = True
        return result

    def compile_formula(self, node, measure, target, refs):
        from ..formula_language.sql import FormulaSqlCompiler

        return (
            FormulaSqlCompiler(self, measure, target, refs)
            .compile(node)
            .sql(dialect="clickhouse")
        )

    def compile_calculated_measure(self, measure, target):
        expression = measure["definition"]["formula"]["expression"]
        refs = {}
        attributes = {}
        counts = {}

        def collect(node):
            if node["type"] == "measureRef":
                refs.setdefault(node["measureId"], "d" + str(len(refs)))
            elif node["type"] == "count":
                from ..formula_language.expressions import count_key

                key = count_key(node)
                counts[key] = node
                refs.setdefault(key, "d" + str(len(refs)))
            elif node["type"] == "attributeRef":
                from .typed_formulas import attribute_alias, attribute_coordinate

                attributes[attribute_alias(node)] = attribute_coordinate(
                    self, node, target
                )
            elif node["type"] == "binary":
                collect(node["left"])
                collect(node["right"])
            elif node["type"] == "call":
                for arg in node["arguments"]:
                    collect(arg)

        collect(expression)
        joins = []
        dependencies = {}
        for mid, alias in refs.items():
            if mid in counts:
                from .counting import CountCompiler

                count_cache_key = (
                    mid,
                    measure["definition"]["nativePlanningLevelId"],
                    target,
                )
                if count_cache_key not in self.cache:
                    self.cache[count_cache_key] = CountCompiler(self).compile(
                        counts[mid], measure, target
                    )
                dep = self.cache[count_cache_key]
            else:
                dep = self.compile_measure_at_level(mid, target)
            dependencies[mid] = dep
            joins.append(
                f"LEFT JOIN {q(dep)} {alias} ON " + self.join_keys("p", alias, target)
            )
        population = self.population(target)
        if attributes:
            seed = self.cte(
                f"SELECT {', '.join(q(key) for key in self.keys(target)) + ', ' if self.keys(target) else ''}NULL AS {q(VALUE)} FROM ({population})"
            )
            population = self.enrich(seed, target, attributes)
        value = self.compile_formula(expression, measure, target, refs)
        flags = [
            f"coalesce({alias}.{q(COMPLETE)},{1 if mid in counts else 0})"
            for mid, alias in refs.items()
            if dependencies[mid] in self.coverage_nodes
        ]
        mixed_flags = [
            f"coalesce({alias}.{q(COMPLETE)},1)=2"
            for mid, alias in refs.items()
            if dependencies[mid] in self.mixed_nodes
        ]
        mixed = " OR ".join(mixed_flags) if mixed_flags else "0"
        flag = " AND ".join(flags) if flags else "1"
        if flags:
            value = f"if({flag},{value},NULL)"
        if mixed_flags:
            value = f"if({mixed},NULL,{value})"
        flag = f"if({mixed},toUInt8(2),toUInt8({flag}))"
        keys = ", ".join("p." + q(k) + " AS " + q(k) for k in self.keys(target))
        keys = keys + ", " if keys else ""
        result = self.cte(
            f"SELECT {keys}{value} AS {q(VALUE)}, toUInt8({flag}) AS {q(COMPLETE)} FROM ({population}) p {' '.join(joins)}"
        )
        if flags:
            self.coverage_nodes[result] = True
        if mixed_flags or measure["definition"]["resultType"]["type"] in {
            "string",
            "date",
            "boolean",
        }:
            self.mixed_nodes.add(result)
        return result

    def compile_seed_measure(self, measure_id):
        key = (measure_id, "__native_seed")
        if key in self.cache:
            return self.cache[key]
        if key in self.visiting:
            raise MetadataError(
                "measure_dependency_cycle",
                "Native measure/formula/basis dependencies contain a cycle.",
            )
        self.visiting.add(key)
        try:
            measure = self.measures[measure_id]
            result = (
                self.compile_native_measure(measure)
                if measure["definition"]["measureType"] == "referenced"
                else self.compile_calculated_measure(
                    measure, measure["definition"]["nativePlanningLevelId"]
                )
            )
            self.cache[key] = result
            return result
        finally:
            self.visiting.remove(key)

    def compile_measure_at_level(self, measure_id, target):
        key = (measure_id, target)
        if key in self.cache:
            return self.cache[key]
        if key in self.visiting:
            raise MetadataError(
                "measure_dependency_cycle",
                "Measure propagation/basis/formula dependencies contain a cycle.",
            )
        self.visiting.add(key)
        try:
            measure = self.measures[measure_id]
            d = measure["definition"]
            native = d["nativePlanningLevelId"]
            relation = self.relationships.analyze(native, target)["relationship"]
            if d.get("calculationType") == "AggregateFirst":
                from ..formula_language.expressions import contains_counts

                if contains_counts(d["formula"]["expression"]):
                    counting = d["nativePlanningLevelId"]
                    if not counting or self.relationships.analyze(counting, target)[
                        "relationship"
                    ] not in {"same", "equivalent", "roll_up"}:
                        raise MetadataError(
                            "measure_path_unavailable",
                            "Counts can only recalculate at the measure’s Output Planning Level or coarser.",
                        )
                    relation = "same"
                via_base = all(
                    self.relationships.analyze(self.base_id, lid)["relationship"]
                    in ("same", "equivalent", "roll_up")
                    for lid in (native, target)
                )
                if relation == "incompatible" and not via_base:
                    raise MetadataError(
                        "measure_path_unavailable",
                        "No proven path to this calculation grain.",
                    )
                result = self.compile_calculated_measure(measure, target)
                operation = "recalculate"
            elif d["resultType"]["type"] in {"string", "date", "boolean"}:
                if target == native:
                    result = self.compile_seed_measure(measure_id)
                    operation = "native_typed"
                elif relation in {"same", "equivalent", "roll_up"}:
                    result = self.rollup(
                        self.compile_seed_measure(measure_id), measure, native, target
                    )
                    operation = "aggregate_typed"
                else:
                    raise MetadataError(
                        "measure_path_unavailable",
                        "String, Date and Boolean results cannot be distributed to finer levels.",
                    )
            elif target == self.base_id:
                if native == target:
                    result = self.compile_seed_measure(measure_id)
                    operation = "native"
                elif relation == "roll_down":
                    result = self.disaggregate(
                        self.compile_seed_measure(measure_id), measure, native, target
                    )
                    operation = "disaggregate"
                elif relation in ("same", "equivalent", "roll_up"):
                    result = self.rollup(
                        self.compile_seed_measure(measure_id), measure, native, target
                    )
                    operation = "aggregate_to_base"
                else:
                    raise MetadataError(
                        "measure_path_unavailable",
                        "Required key lineage or dimension binding to Base is missing.",
                    )
            elif (
                self.relationships.analyze(self.base_id, target)["relationship"]
                in (
                    "roll_up",
                    "equivalent",
                )
                and self.relationships.analyze(native, self.base_id)["relationship"]
                != "incompatible"
            ):
                result = self.rollup(
                    self.compile_measure_at_level(measure_id, self.base_id),
                    measure,
                    self.base_id,
                    target,
                )
                coverage = self.coverage_nodes.get(result)
                if coverage:
                    fallback = None
                    if relation in ("same", "equivalent", "roll_up"):
                        fallback = self.compile_seed_measure(measure_id)
                        if native != target:
                            fallback = self.rollup(fallback, measure, native, target)
                    result = self.with_coverage(result, coverage, target, fallback)
                operation = "via_base"
            elif target == native:
                result = self.compile_seed_measure(measure_id)
                operation = "native_without_base_path"
            elif relation in ("same", "equivalent", "roll_up"):
                result = self.rollup(
                    self.compile_seed_measure(measure_id), measure, native, target
                )
                operation = "aggregate_without_base_path"
            else:
                raise MetadataError(
                    "measure_path_unavailable",
                    "Required key lineage or dimension binding is missing.",
                )
            result = self.quantize_node(result, measure, target)
            self.cache[key] = result
            self.nodes.append(MeasureNode(measure_id, target, operation))
            return result
        finally:
            self.visiting.remove(key)

    def rounded_value(self, expression, measure):
        dtype = measure["definition"]["resultType"]
        if dtype["type"] in {"string", "date", "boolean"}:
            return expression
        if dtype["type"] == "decimal":
            return f"roundBankers({expression},{min(6, dtype['scale'])})"
        number = f"toFloat64({expression})"
        # Large Float64 values already have fewer than six fractional digits;
        # avoid overflowing a scale-up inside the rounding implementation.
        return f"if(abs({number})>=1e15,{number},roundBankers({number},6))"

    def quantize_node(self, query, measure, level):
        """Downstream representations use the same six-place values we store."""
        selected = ["r." + q(k) + " AS " + q(k) for k in self.keys(level)]
        selected.append(
            self.rounded_value("r." + q(VALUE), measure) + " AS " + q(VALUE)
        )
        flag = (
            "r." + q(COMPLETE)
            if query in self.coverage_nodes or query in self.mixed_nodes
            else "toUInt8(1)"
        )
        selected.append(flag + " AS " + q(COMPLETE))
        result = self.cte(f"SELECT {', '.join(selected)} FROM {q(query)} r")
        if query in self.coverage_nodes:
            self.coverage_nodes[result] = True
        if query in self.mixed_nodes:
            self.mixed_nodes.add(result)
        return result

    def cast_value(self, expression, measure):
        dtype = measure["definition"]["resultType"]
        if dtype["type"] in {"string", "date", "boolean"}:
            typ = {"string": "String", "date": "Date32", "boolean": "Bool"}[
                dtype["type"]
            ]
            converted = f"accurateCastOrNull({expression},{self.literal(typ)})"
            guard = f"throwIf(isNotNull({expression}) AND isNull({converted}),'SEMANTIC_MEASURE_OVERFLOW')"
            if not dtype["nullable"]:
                guard += f" + throwIf(isNull({expression}),'SEMANTIC_MEASURE_NULL')"
            return f"if(({guard})=0,{converted},NULL)", typ
        if dtype["type"] == "decimal":
            typ = f"Decimal({dtype['precision']},{dtype['scale']})"
            rounded = self.rounded_value(expression, measure)
            # ClickHouse Int256 -> Decimal accurate casts are unsupported. String
            # conversion handles that input; an explicit bound enforces logical
            # precision (ClickHouse also permits the backing integer's extra digits).
            parsed = f"accurateCastOrNull(toString({rounded}),{self.literal(typ)})"
            integer_digits = dtype["precision"] - dtype["scale"]
            maximum = ("9" * integer_digits or "0") + (
                "." + "9" * dtype["scale"] if dtype["scale"] else ""
            )
            bound = f"toDecimal256({self.literal(maximum)},{dtype['scale']})"
            converted = (
                f"if(({parsed}) > {bound} OR ({parsed}) < -{bound},NULL,{parsed})"
            )
        elif dtype["type"] == "integer":
            typ = "Int64"
            converted = f"accurateCastOrNull({expression},'Int64')"
            converted = f"if(({expression})!=trunc({expression}),NULL,{converted})"
        else:
            typ = "Float64"
            converted = f"if(isFinite(toFloat64({expression})),{self.rounded_value(expression, measure)},NULL)"
        guard = f"throwIf(isNotNull({expression}) AND isNull({converted}),'SEMANTIC_MEASURE_OVERFLOW')"
        if not dtype["nullable"]:
            guard += f" + throwIf(isNull({expression}),'SEMANTIC_MEASURE_NULL')"
        expression = f"if(({guard})=0,{converted},NULL)"
        if not dtype["nullable"]:
            expression = f"if(({guard})=0,assumeNotNull({converted}),CAST(0 AS {typ}))"
        return expression, typ

    def cast_node_value(self, alias, node, measure):
        expression = alias + "." + q(VALUE)
        if node not in self.coverage_nodes and node not in self.mixed_nodes:
            return self.cast_value(expression, measure)
        # Unavailable finer cells are separate from the native NULL contract.
        # Preserve the guard on complete cells; unknown distributions may be NULL.
        from copy import deepcopy

        nullable = deepcopy(measure)
        nullable["definition"]["resultType"]["nullable"] = True
        value, typ = self.cast_value(expression, nullable)
        if not measure["definition"]["resultType"]["nullable"]:
            guard = f"throwIf(coalesce({alias}.{q(COMPLETE)},0)=1 AND isNull({expression}),'SEMANTIC_MEASURE_NULL')"
            value = f"if(({guard})=0,{value},NULL)"
        return value, typ

    def compile_level(self, target):
        available, unavailable = {}, {}
        for mid, measure in self.measures.items():
            checkpoint = (
                dict(self.ctes),
                dict(self.cache),
                dict(self.transaction_relations),
                list(self.assertions),
                set(self._lookup_checks),
                list(self.nodes),
                dict(self.coverage_nodes),
                set(self.mixed_nodes),
            )
            try:
                available[mid] = self.compile_measure_at_level(mid, target)
            except MetadataError as exc:
                if exc.code not in (
                    "measure_path_unavailable",
                    "formula_grain_unavailable",
                ):
                    raise
                (
                    self.ctes,
                    self.cache,
                    self.transaction_relations,
                    self.assertions,
                    self._lookup_checks,
                    self.nodes,
                    self.coverage_nodes,
                    self.mixed_nodes,
                ) = checkpoint
                unavailable[mid] = {"code": exc.code, "message": exc.message}
                if measure["definition"]["nativePlanningLevelId"] == target:
                    raise
        columns = [dict(c) for c in self.populations[target]["columns"]]
        used = {c["name"] for c in columns}
        selected = ["p." + q(c["columnId"]) + " AS " + q(c["name"]) for c in columns]
        joins = []
        for index, (mid, node) in enumerate(available.items()):
            measure = self.measures[mid]
            name = measure["name"]
            suffix = index + 1
            while name in used:
                name = "Measure__" + measure["name"] + "__" + str(suffix)
                suffix += 1
            used.add(name)
            alias = "v" + str(index)
            value, typ = self.cast_node_value(alias, node, measure)
            selected.append(value + " AS " + q(name))
            joins.append(
                f"LEFT JOIN {q(node)} {alias} ON " + self.join_keys("p", alias, target)
            )
            columns.append(
                {
                    "columnId": mid,
                    "name": name,
                    "displayName": measure.get("displayName", measure["name"]),
                    "physicalType": typ,
                    "nullable": node in self.coverage_nodes
                    or measure["definition"]["resultType"]["nullable"],
                    "role": "measure",
                    "measureId": mid,
                    "nativePlanningLevelId": measure["definition"][
                        "nativePlanningLevelId"
                    ],
                    "editableAtLevel": self.relationships.can_edit(measure, target),
                }
            )
        for index, (mid, node) in enumerate(available.items()):
            coverage = self.coverage_nodes.get(node)
            if not coverage:
                continue
            name = "__coverage_" + mid
            while name in used:
                name += "_"
            used.add(name)
            alias = "v" + str(index)
            selected.append(f"toUInt8(coalesce({alias}.{q(COMPLETE)},0)) AS {q(name)}")
            columns.append(
                {
                    "columnId": "coverage_" + mid,
                    "name": name,
                    "physicalType": "UInt8",
                    "nullable": False,
                    "role": "coverage",
                    "coverageFor": mid,
                }
            )
        for index, (mid, node) in enumerate(available.items()):
            if node not in self.mixed_nodes:
                continue
            name = "__mixed_" + mid
            while name in used:
                name += "_"
            used.add(name)
            selected.append(
                f"toUInt8(coalesce(v{index}.{q(COMPLETE)},1)=2) AS {q(name)}"
            )
            columns.append(
                {
                    "columnId": "mixed_" + mid,
                    "name": name,
                    "physicalType": "UInt8",
                    "nullable": False,
                    "role": "mixed",
                    "mixedFor": mid,
                }
            )
        sql = self.with_sql(
            f"SELECT {', '.join(selected)} FROM ({self.population(target)}) p {' '.join(joins)}"
        )
        checks = tuple(Check(c.code, self.with_sql(c.sql)) for c in self.assertions)
        return ExecutionPlan(
            parse_one(sql, read="clickhouse"),
            checks,
            RelationSchema(tuple(columns)),
            MEASURE_COMPILER_VERSION,
        ), unavailable
