from ...core.errors import MetadataError
from ...warehouse.sql import quote_identifier as q
from ..graph.relationships import Coordinate
from ..sql.contracts import Check
from ..transformations.filters import dimension_filter_columns
from .aggregation import AggregationOperator, AggregationPlan
from .contracts import COMPLETE, VALUE


class MeasureAlignmentCompiler:
    """Enrich coordinates and move values between proven grains."""

    def __init__(self, compiler):
        self.compiler = compiler

    def enrich(self, query, level, coordinates):
        """Map logical coordinates without requiring dimension attributes to be exposed."""
        selected = ["r." + q(k) + " AS " + q(k) for k in self.compiler.keys(level)] + [
            "r." + q(VALUE) + " AS " + q(VALUE),
            (
                "r." + q(COMPLETE)
                if query in self.compiler.coverage_nodes
                or query in self.compiler.mixed_nodes
                else "toUInt8(1)"
            )
            + " AS "
            + q(COMPLETE),
        ]
        joins = []
        dimension_members = {}
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
                dim = self.compiler.objects[dim_id]
                definition = dim["definition"]
                if dim_id not in dimension_members:
                    binding = next(
                        (
                            b
                            for b in self.compiler.levels[level]["definition"][
                                "memberBindings"
                            ]
                            if b["memberCatalogId"] == dim_id
                        )
                    )
                    source = self.compiler.managed[definition["sourceId"]]
                    input_table, input_cols = self.compiler.object_input(dim, source)
                    columns = {c["columnId"]: c for c in input_cols}
                    alias_dim = "lookup_" + str(len(dimension_members))
                    dimension_members[dim_id] = alias_dim
                    dim_sql = (
                        "SELECT "
                        + ", ".join(
                            (
                                q(columns[c["sourceColumnId"]]["name"])
                                + " AS "
                                + q(c["columnId"])
                                for c in definition["columns"]
                            )
                        )
                        + " FROM "
                        + input_table
                    )
                    if definition.get("filters"):
                        dim_sql = f"SELECT * FROM ({dim_sql}) WHERE {self.compiler.filter_sql(definition['filters'], dimension_filter_columns(definition, input_cols))}"
                    dim_sql = self.compiler.reduced_lookup(
                        dim_sql,
                        [m["memberColumnId"] for m in binding["keyMappings"]],
                        [
                            c.dimension_column_id
                            for c in coordinates.values()
                            if c and c.dimension_id == dim_id
                        ],
                    )
                    on = " AND ".join(
                        (
                            "p."
                            + q(m.get("grainColumnId") or m.get("populationColumnId"))
                            + " = "
                            + alias_dim
                            + "."
                            + q(m["memberColumnId"])
                            for m in binding["keyMappings"]
                        )
                    )
                    joins.append(
                        f"LEFT JOIN (SELECT *,toUInt8(1) AS __coordinate_match FROM ({dim_sql})) {alias_dim} ON {on}"
                    )
                    selected.append(
                        f"throwIf(coalesce({alias_dim}.__coordinate_match,0)=0,'SEMANTIC_UNMATCHED_DIMENSION') AS {q('__coordinate_guard_' + alias_dim)}"
                    )
                    marker = (level, dim_id)
                    if marker not in self.compiler._lookup_checks:
                        self.compiler._lookup_checks.add(marker)
                        keys = ", ".join(
                            (q(m["memberColumnId"]) for m in binding["keyMappings"])
                        )
                        self.compiler.assertions.append(
                            Check(
                                "ambiguous_dimension_lookup",
                                f"SELECT {keys},count() FROM ({dim_sql}) GROUP BY {keys} HAVING count()>1 LIMIT 20",
                            )
                        )
                expression = (
                    dimension_members[dim_id] + "." + q(coordinate.dimension_column_id)
                )
            selected.append(expression + " AS " + q(alias))
        guards = [
            f"throwIf(coalesce({a}.__coordinate_match,0)=0,'SEMANTIC_UNMATCHED_DIMENSION')=0"
            for a in dimension_members.values()
        ]
        return (
            f"SELECT {', '.join(selected)} FROM {q(query)} r INNER JOIN ({self.compiler.population(level)}) p ON {self.compiler.join_keys('r', 'p', level)} {' '.join(joins)}"
            + (" WHERE " + " AND ".join(guards) if guards else "")
        )

    def ordering(self, level):
        for dim in self.compiler.relationships.dimension_members.values():
            if not dim["isTimeDim"]:
                continue
            cid = dim["definition"]["time"]["orderingColumnId"]
            col = next(
                (c for c in dim["definition"]["columns"] if c["columnId"] == cid)
            )
            symbol = Coordinate(
                frozenset(
                    {
                        ("dimensionMembers", dim["objectId"], cid),
                        (
                            "source",
                            dim["definition"]["sourceId"],
                            col["sourceColumnId"],
                        ),
                    }
                ),
                is_time=True,
            )
            found = self.compiler.relationships.resolve(level, symbol)
            if found:
                return found
        return None

    def rollup(self, query, measure, source, target):
        mapping = self.compiler.relationships.mapping(source, target)
        coordinates = {"__target_" + k: v for k, v in mapping.items()}
        source_outputs = self.compiler.relationships.outputs(source)
        removed = [
            k
            for k in self.compiler.keys(source)
            if self.compiler.relationships.resolve(target, source_outputs[k]) is None
        ]
        time_removed = any((source_outputs[k].is_time for k in removed))
        non_time_removed = any((not source_outputs[k].is_time for k in removed))
        policy = measure["definition"]["aggregation"]
        has_mixed = query in self.compiler.mixed_nodes
        order_symbol = (
            self.compiler.ordering(source)
            if time_removed and policy["time"] in ("first", "last")
            else None
        )
        if order_symbol:
            coordinates["__time_order"] = order_symbol
        data = self.compiler.enrich(query, source, coordinates)
        target_ids = ["__target_" + k for k in self.compiler.keys(target)]
        time_ids = [k for k in self.compiler.keys(source) if source_outputs[k].is_time]
        non_time_ids = [
            k for k in self.compiler.keys(source) if not source_outputs[k].is_time
        ]
        aggregation = AggregationOperator.compile(
            self.compiler,
            data,
            AggregationPlan(
                tuple(target_ids),
                tuple(time_ids),
                tuple(non_time_ids),
                time_removed,
                non_time_removed,
                policy,
                bool(order_symbol),
            ),
            has_mixed,
        )
        data, has_mixed = (aggregation.sql, aggregation.has_mixed)
        mapped = self.compiler.cte(
            f"SELECT {', '.join((q('__target_' + k) + ' AS ' + q(k) for k in self.compiler.keys(target)))}, {q(VALUE)}, {q(COMPLETE)} FROM ({data})"
        )
        if query in self.compiler.coverage_nodes:
            self.compiler.coverage_nodes[mapped] = True
        if has_mixed:
            self.compiler.mixed_nodes.add(mapped)
        return self.compiler.align(mapped, target)

    def align(self, query, level):
        flag = (
            f"coalesce(r.{q(COMPLETE)},1)"
            if query in self.compiler.coverage_nodes
            or query in self.compiler.mixed_nodes
            else "toUInt8(1)"
        )
        result = self.compiler.cte(
            f"SELECT {', '.join(('p.' + q(k) + ' AS ' + q(k) for k in self.compiler.keys(level)))}, r.{q(VALUE)} AS {q(VALUE)}, {flag} AS {q(COMPLETE)} FROM ({self.compiler.population(level)}) p LEFT JOIN {q(query)} r ON {self.compiler.join_keys('p', 'r', level)}"
        )
        if query in self.compiler.coverage_nodes:
            self.compiler.coverage_nodes[result] = True
        if query in self.compiler.mixed_nodes:
            self.compiler.mixed_nodes.add(result)
        return result

    def disaggregate(self, parent, measure, source, target):
        # Source visibility uses aggregation-derived distribution. Editable allocation is separate.
        """Initialise finer values from aggregation, never from edit allocation."""
        mapping = self.compiler.relationships.mapping(target, source)
        child = self.compiler.cte(
            "SELECT "
            + ", ".join((q(k) for k in self.compiler.keys(target)))
            + f", NULL AS {q(VALUE)} FROM ({self.compiler.population(target)})"
        )
        child = self.compiler.cte(
            self.compiler.enrich(
                child, target, {"__parent_" + k: c for k, c in mapping.items()}
            )
        )
        parent_ids = ["__parent_" + k for k in self.compiler.keys(source)]
        on = " AND ".join(
            (
                "c." + q("__parent_" + k) + " = p." + q(k)
                for k in self.compiler.keys(source)
            )
        )
        grouped = ", ".join((q(k) for k in parent_ids))
        self.compiler.assertions.append(
            Check(
                "no_disaggregation_children",
                f"SELECT {', '.join(('p.' + q(k) for k in [*self.compiler.keys(source), VALUE]))} FROM {q(parent)} p LEFT JOIN (SELECT {grouped},toUInt8(1) AS __matched FROM {q(child)} GROUP BY {grouped}) c ON {on} WHERE coalesce(c.__matched,0)=0 AND isNotNull(p.{q(VALUE)}) LIMIT 20",
            )
        )
        outputs = self.compiler.relationships.outputs(target)
        expansion_axes = self.compiler.relationships.expansion_axes(source, target)
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
                k
                for k in self.compiler.keys(target)
                if outputs[k].is_time == (axis == "time")
            ]
            if policy[axis] == "sum":
                partition = ", ".join(("c." + q(k) for k in [*parent_ids, *expanded]))
                members = ", ".join(("c." + q(k) for k in axis_keys))
                count = f"uniqExact(tuple({members})) OVER (PARTITION BY {partition})"
                value = self.compiler.divide(value, count, measure)
            expanded.extend(axis_keys)
        flag = (
            f"coalesce(p.{q(COMPLETE)},0)"
            if parent in self.compiler.coverage_nodes
            else "toUInt8(1)"
        )
        keys = ", ".join(
            ("c." + q(k) + " AS " + q(k) for k in self.compiler.keys(target))
        )
        result = self.compiler.cte(
            f"SELECT {keys}, {value} AS {q(VALUE)}, {flag} AS {q(COMPLETE)} FROM {q(child)} c LEFT JOIN {q(parent)} p ON {on}"
        )
        if parent in self.compiler.coverage_nodes:
            self.compiler.coverage_nodes[result] = True
        return result

    def with_coverage(self, query, coverage, level, fallback=None):
        """Recover incomplete groups from a proven native representation."""
        if not fallback:
            return query
        keys = ", ".join(
            ("r." + q(k) + " AS " + q(k) for k in self.compiler.keys(level))
        )
        keys = keys + ", " if keys else ""
        complete = f"r.{q(COMPLETE)}"
        fc = (
            f"coalesce(f.{q(COMPLETE)},0)"
            if fallback in self.compiler.coverage_nodes
            else "1"
        )
        value = f"if({complete},r.{q(VALUE)},if({fc},f.{q(VALUE)},NULL))"
        result = self.compiler.cte(
            f"SELECT {keys}{value} AS {q(VALUE)}, toUInt8({complete} OR ({fc})) AS {q(COMPLETE)} FROM {q(query)} r LEFT JOIN {q(fallback)} f ON {self.compiler.join_keys('r', 'f', level)}"
        )
        self.compiler.coverage_nodes[result] = True
        return result
