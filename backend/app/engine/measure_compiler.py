from .table_storage import GENERATION, column_sql, token
from ..warehouse.repository import quote_literal as lit
from ..services.measure_types import normalize_measure
from dataclasses import dataclass

from sqlglot import parse_one

from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q
from .contracts import Check, ExecutionPlan, RelationSchema
from .planning_compiler import PlanningLevelCompiler
from .relationships import Coordinate, LevelRelationshipAnalyzer

VALUE = "__value"
COMPLETE = "__complete"
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
        self.objects = {o["objectId"]: normalize_measure(o) for o in objects}
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
        self.ctes, self.cache, self.visiting, self.nodes = {}, {}, set(), []
        self.coverage_nodes = {}
        self.assertions = []
        self.max_nodes = 1024
        self._lookup_checks = set()

    def keys(self, level):
        return self.levels[level]["definition"]["grain"]["keyIds"]

    def population(self, level):
        artifact = self.populations[level]
        return f"SELECT {', '.join(column_sql(c) + ' AS ' + q(c['columnId']) for c in artifact['columns'])} FROM {self.table(artifact['physicalName'])} WHERE {q(GENERATION)}={lit(token(artifact))}"

    def cte(self, sql):
        if len(self.ctes) >= self.max_nodes:
            raise MetadataError(
                "measure_graph_limit",
                "Measure execution exceeds the configured dependency-node limit.",
            )
        name = "measure_node_" + str(len(self.ctes))
        self.ctes[name] = sql
        return name

    def with_sql(self, sql):
        return (
            "WITH "
            + ", ".join(q(k) + " AS (" + v + ")" for k, v in self.ctes.items())
            + " "
            + sql
            if self.ctes
            else sql
        )

    def join_keys(self, left, right, level):
        return " AND ".join(f"{left}.{q(k)} = {right}.{q(k)}" for k in self.keys(level))

    def source_columns(self, source):
        return {c["columnId"]: c for c in self.managed[source]["columns"]}

    def widen(self, expression, measure):
        dtype = measure["definition"]["resultType"]
        if dtype["type"] == "integer":
            return f"toInt256({expression})"
        if dtype["type"] == "decimal":
            return f"toDecimal256({expression}, {dtype['scale']})"
        return f"toFloat64({expression})"

    def divide(self, left, right, measure):
        if measure["definition"]["resultType"]["type"] == "number":
            return f"({left}) / nullIf(({right}),0)"
        scale = min(60, max(18, measure["definition"]["resultType"].get("scale") or 0))
        return f"divideDecimal(toDecimal256({left},{scale}), nullIf(toDecimal256({right},{scale}),0), {scale})"

    def aggregate(self, expression, method, policy, ordering=None):
        if method in ("first", "last"):
            if not ordering:
                raise MetadataError(
                    "time_order_required",
                    "First/last aggregation requires an explicit Time ordering mapping.",
                )
            function = "argMinIf" if method == "first" else "argMaxIf"
            inner = f"tupleElement({function}(tuple({expression}), {ordering}, isNotNull({expression})),1)"
        elif method == "count_distinct":
            inner = f"uniqExact({expression})"
        elif method == "count":
            inner = f"count({expression})"
        else:
            inner = f"{method}({expression})"
        empty = "0" if policy["allNullResult"] == "zero" else "NULL"
        result = f"if(countIf(isNotNull({expression}))=0,{empty},{inner})"
        if policy["nullInputPolicy"] == "propagate":
            result = f"if(countIf(isNull({expression}))>0,NULL,{result})"
        return result

    def resolve_transaction(self, transaction_id):
        from .transaction_resolution import TransactionResolutionCompiler

        if transaction_id not in self.transaction_relations:
            tx = self.objects[transaction_id]
            if (
                tx["definition"].get("calendarResolution")
                and tx["definition"].get("mappingMode") != "resolve_multi_grain"
            ):
                from .calendar_resolution import CalendarResolutionCompiler
                from .transaction_resolution import ResolvedTransaction

                c = CalendarResolutionCompiler(self, tx)
                node = c.compile()
                cols = [{**col, "storageName": col["columnId"]} for col in c.p.schema()]
                result = ResolvedTransaction(
                    node, cols, tx["definition"]["keyMappings"], node, [node], node
                )
                self.transaction_relations[transaction_id] = result
                return result
            if transaction_id not in self.resolution_profiles:
                raise MetadataError(
                    "resolution_not_prepared",
                    "Prepare the transaction matching profiles before compiling measures.",
                )
            self.transaction_relations[transaction_id] = TransactionResolutionCompiler(
                self,
                tx,
                self.resolution_profiles[transaction_id],
                self.resolution_max_rows,
            ).compile()
        return self.transaction_relations[transaction_id]

    def native(self, measure):
        d = measure["definition"]
        level = d["nativePlanningLevelId"]
        tx = self.objects[d["transactionId"]]["definition"]
        if tx.get("mappingMode") == "resolve_multi_grain" or tx.get(
            "calendarResolution"
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
        grouped = None
        if tx.get("mappingMode") == "resolve_multi_grain" or tx.get(
            "calendarResolution"
        ):
            from .date_range_resolution import RangeMeasureAggregator

            grouped = RangeMeasureAggregator(
                self, self.objects[d["transactionId"]], resolved, measure
            ).compile()
        if grouped is None:
            grouped = self.cte(
                f"SELECT {selected}, {agg} AS {q(VALUE)}, toUInt8(1) AS __present FROM {table} GROUP BY {group}"
            )
        self.assertions.append(
            Check(
                "unmatched_transaction_grain",
                f"SELECT a.* FROM {q(grouped)} a LEFT JOIN (SELECT *,toUInt8(1) AS __matched FROM ({self.population(level)})) p ON {self.join_keys('a', 'p', level)} WHERE coalesce(p.__matched,0)=0 LIMIT 20",
            )
        )
        absent = "0" if d["missingTransactionValue"] == "zero" else "NULL"
        return self.cte(
            f"SELECT {', '.join('p.' + q(k) + ' AS ' + q(k) for k in self.keys(level))}, if(coalesce(a.__present,0)=0,{absent},a.{q(VALUE)}) AS {q(VALUE)} FROM ({self.population(level)}) p LEFT JOIN {q(grouped)} a ON {self.join_keys('p', 'a', level)}"
        )

    def enrich(self, query, level, coordinates):
        """Map logical coordinates without requiring dimension attributes to be exposed."""
        selected = ["r." + q(k) + " AS " + q(k) for k in self.keys(level)] + [
            "r." + q(VALUE) + " AS " + q(VALUE),
            ("r." + q(COMPLETE) if query in self.coverage_nodes else "toUInt8(1)")
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
                    columns = {c["columnId"]: c for c in source["columns"]}
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
                        + self.table(source["physicalName"])
                    )
                    if definition.get("filters"):
                        dim_sql = f"SELECT * FROM ({dim_sql}) WHERE {self.filter_sql(definition['filters'], [c['columnId'] for c in definition['columns']])}"
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
                q(VALUE),
                policy[stage],
                policy,
                q("__time_order") if order_symbol else None,
            )
            order_extra = (
                ", min(__time_order) AS __time_order"
                if order_symbol and stage == "nonTime" and pending["time"]
                else ""
            )
            data = f"SELECT {', '.join(q(k) for k in groups)}, if(min(aggregation_input.{q(COMPLETE)}),{value},NULL) AS {q(VALUE)}, min(aggregation_input.{q(COMPLETE)}) AS {q(COMPLETE)}{order_extra} FROM ({data}) AS aggregation_input GROUP BY {', '.join(q(k) for k in groups)}"
        mapped = self.cte(
            f"SELECT {', '.join(q('__target_' + k) + ' AS ' + q(k) for k in self.keys(target))}, {q(VALUE)}, {q(COMPLETE)} FROM ({data})"
        )
        if query in self.coverage_nodes:
            self.coverage_nodes[mapped] = True
        return self.align(mapped, target)

    def align(self, query, level):
        flag = (
            f"coalesce(r.{q(COMPLETE)},1)"
            if query in self.coverage_nodes
            else "toUInt8(1)"
        )
        result = self.cte(
            f"SELECT {', '.join('p.' + q(k) + ' AS ' + q(k) for k in self.keys(level))}, r.{q(VALUE)} AS {q(VALUE)}, {flag} AS {q(COMPLETE)} FROM ({self.population(level)}) p LEFT JOIN {q(query)} r ON {self.join_keys('p', 'r', level)}"
        )
        if query in self.coverage_nodes:
            self.coverage_nodes[result] = True
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

    def formula(self, node, measure, target, refs):
        kind = node["type"]
        if kind == "literal":
            return self.literal(node["value"])
        if kind == "measureRef":
            return refs[node["measureId"]] + "." + q(VALUE)
        if kind == "grainRef":
            coordinate = self.relationships.outputs(
                measure["definition"]["nativePlanningLevelId"]
            ).get(node["grainColumnId"])
            resolved = (
                self.relationships.resolve(target, coordinate) if coordinate else None
            )
            if not resolved or not resolved.column_id:
                raise MetadataError(
                    "formula_grain_unavailable",
                    "A formula grain reference is unavailable at the requested level.",
                )
            return "p." + q(resolved.column_id)
        if kind == "binary":
            left = self.formula(node["left"], measure, target, refs)
            right = self.formula(node["right"], measure, target, refs)
            if node["operator"] == "divide":
                return self.divide(left, right, measure)
            op = {"add": "+", "subtract": "-", "multiply": "*"}[node["operator"]]
            return f"({left}) {op} ({right})"
        args = [self.formula(arg, measure, target, refs) for arg in node["arguments"]]
        fn = {
            "abs": "abs",
            "round": "roundBankers",
            "coalesce": "coalesce",
            "min": "least",
            "max": "greatest",
        }[node["function"]]
        # ClickHouse least/greatest can ignore NULLs; formula null semantics are propagate.
        value = fn + "(" + ", ".join(args) + ")"
        if node["function"] in ("min", "max"):
            value = (
                "if("
                + " OR ".join("isNull(" + a + ")" for a in args)
                + ",NULL,"
                + value
                + ")"
            )
        return value

    def calculated(self, measure, target):
        expression = measure["definition"]["formula"]["expression"]
        refs = {}

        def collect(node):
            if node["type"] == "measureRef":
                refs.setdefault(node["measureId"], "d" + str(len(refs)))
            elif node["type"] == "binary":
                collect(node["left"])
                collect(node["right"])
            elif node["type"] == "call":
                for arg in node["arguments"]:
                    collect(arg)

        collect(expression)
        joins = []
        for mid, alias in refs.items():
            dep = self.node(mid, target)
            joins.append(
                f"LEFT JOIN {q(dep)} {alias} ON " + self.join_keys("p", alias, target)
            )
        value = self.formula(expression, measure, target, refs)
        flags = [
            f"coalesce({alias}.{q(COMPLETE)},0)"
            for mid, alias in refs.items()
            if self.node(mid, target) in self.coverage_nodes
        ]
        flag = " AND ".join(flags) if flags else "1"
        if flags:
            value = f"if({flag},{value},NULL)"
        keys = ", ".join("p." + q(k) + " AS " + q(k) for k in self.keys(target))
        keys = keys + ", " if keys else ""
        result = self.cte(
            f"SELECT {keys}{value} AS {q(VALUE)}, toUInt8({flag}) AS {q(COMPLETE)} FROM ({self.population(target)}) p {' '.join(joins)}"
        )
        if flags:
            self.coverage_nodes[result] = True
        return result

    def seed(self, measure_id):
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
                self.native(measure)
                if measure["definition"]["measureType"] == "referenced"
                else self.calculated(
                    measure, measure["definition"]["nativePlanningLevelId"]
                )
            )
            self.cache[key] = result
            return result
        finally:
            self.visiting.remove(key)

    def node(self, measure_id, target):
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
                result = self.calculated(measure, target)
                operation = "recalculate"
            elif target == self.base_id:
                if native == target:
                    result = self.seed(measure_id)
                    operation = "native"
                elif relation == "roll_down":
                    result = self.disaggregate(
                        self.seed(measure_id), measure, native, target
                    )
                    operation = "disaggregate"
                elif relation in ("same", "equivalent", "roll_up"):
                    result = self.rollup(self.seed(measure_id), measure, native, target)
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
                    self.node(measure_id, self.base_id), measure, self.base_id, target
                )
                coverage = self.coverage_nodes.get(result)
                if coverage:
                    fallback = None
                    if relation in ("same", "equivalent", "roll_up"):
                        fallback = self.seed(measure_id)
                        if native != target:
                            fallback = self.rollup(fallback, measure, native, target)
                    result = self.with_coverage(result, coverage, target, fallback)
                operation = "via_base"
            elif target == native:
                result = self.seed(measure_id)
                operation = "native_without_base_path"
            elif relation in ("same", "equivalent", "roll_up"):
                result = self.rollup(self.seed(measure_id), measure, native, target)
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
        flag = "r." + q(COMPLETE) if query in self.coverage_nodes else "toUInt8(1)"
        selected.append(flag + " AS " + q(COMPLETE))
        result = self.cte(f"SELECT {', '.join(selected)} FROM {q(query)} r")
        if query in self.coverage_nodes:
            self.coverage_nodes[result] = True
        return result

    def cast_value(self, expression, measure):
        dtype = measure["definition"]["resultType"]
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
        if node not in self.coverage_nodes:
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
            )
            try:
                available[mid] = self.node(mid, target)
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
        sql = self.with_sql(
            f"SELECT {', '.join(selected)} FROM ({self.population(target)}) p {' '.join(joins)}"
        )
        checks = tuple(Check(c.code, self.with_sql(c.sql)) for c in self.assertions)
        return ExecutionPlan(
            parse_one(sql, read="clickhouse"),
            checks,
            RelationSchema(tuple(columns)),
            "measure-v4-aggregation-derived-round6-sqlglot-27.28.1",
        ), unavailable
