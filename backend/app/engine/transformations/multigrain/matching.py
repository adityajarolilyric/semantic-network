from ....warehouse.sql import quote_identifier as q
from ....warehouse.sql import quote_literal as lit
from ...sql.contracts import Check


class ResolutionMatchCompiler:
    """Match one constraint profile and preserve its original allocation denominator."""

    def __init__(self, resolution):
        self.resolution = resolution

    def compile(self, prepared, profile):
        h, p = self.resolution.host, self.resolution.planner
        flagged = prepared.profile_node
        seed = prepared.population_node
        payload_ids = prepared.payload_column_ids
        partitions = prepared.partition_column_ids
        payload = prepared.payload_expression
        order = prepared.order_expression
        calendar_enabled = prepared.calendar_enabled
        date_range = prepared.date_range
        selected_rules = []
        for rule, flag in zip(p.rules, profile):
            if flag:
                target = (
                    rule["target"]
                    if rule["kind"] == "column"
                    else rule["selectors"][flag - 1]["target"]
                )
                selected_rules.append(
                    (
                        rule["sourceColumnId"]
                        if rule["kind"] == "column"
                        else rule["valueColumnId"],
                        target,
                    )
                )
        coords = {
            f"__match_attr_{i}": p.target(target)
            for i, (_, target) in enumerate(selected_rules)
        }
        native_sql = h.enrich(seed, self.resolution.level, coords)
        native = h.cte(native_sql)
        original_native = native
        native = date_range.expand_native(native)
        casted = []
        for i, (cid, target) in enumerate(selected_rules):
            coordinate = coords[f"__match_attr_{i}"]
            if coordinate.column_id:
                col = next(
                    (
                        c
                        for c in h.populations[self.resolution.level]["columns"]
                        if c["columnId"] == coordinate.column_id
                    )
                )
            else:
                dim = h.objects[coordinate.dimension_id]["definition"]
                dim_col = next(
                    (
                        c
                        for c in dim["columns"]
                        if c["columnId"] == coordinate.dimension_column_id
                    )
                )
                col = next(
                    (
                        c
                        for c in h.object_input(
                            h.objects[coordinate.dimension_id],
                            h.managed[dim["sourceId"]],
                        )[1]
                        if c["columnId"] == dim_col["sourceColumnId"]
                    )
                )
            typ = col["physicalType"]
            expression = (
                f"toString({q(cid)})"
                if "String" in typ
                else f"accurateCastOrNull(toString({q(cid)}),{lit(typ)})"
            )
            casted.append(expression + " AS " + q(f"__criterion_{i}"))
        casted.extend(date_range.bounds())
        predicate = "__profile = tuple(" + ", ".join((str(v) for v in profile)) + ")"
        typed = h.cte(
            f"SELECT *, {', '.join(casted)} FROM {q(flagged)} WHERE {predicate}"
            if casted
            else f"SELECT * FROM {q(flagged)} WHERE {predicate}"
        )
        criteria = [f"__criterion_{i}" for i in range(len(selected_rules))]
        if criteria:
            h.assertions.append(
                Check(
                    "invalid_resolution_literal",
                    f"SELECT * FROM {q(typed)} WHERE {' OR '.join(('isNull(' + q(c) + ')' for c in criteria))} LIMIT 20",
                )
            )
        group = [*criteria, *date_range.bound_names, *partitions]
        if calendar_enabled:
            group.append(p.calendar.date_id)
        group_sql = ", ".join((q(c) for c in group))
        prefix = group_sql + ", " if group else ""
        h.assertions.append(
            Check(
                "ambiguous_resolution_order",
                f"SELECT {prefix}{order} FROM {q(typed)} GROUP BY {prefix}{order} HAVING count()>1 LIMIT 20",
            )
        )
        latest = h.cte(
            f"SELECT {prefix}argMax({payload},{order}) AS __payload, max({order}) AS __order FROM {q(typed)}"
            + (" GROUP BY " + group_sql if group else "")
        )
        joins = " AND ".join(
            (f"e.{q(c)} = n.{q(f'__match_attr_{i}')}" for i, c in enumerate(criteria))
        )
        native_keys = [
            f"n.{q(k)} AS {q('__native_' + str(i))}"
            for i, k in enumerate(h.keys(self.resolution.level))
        ]
        scope = [f"e.{q(c)} AS {q(c)}" for c in partitions]
        partition = ", ".join(("e." + q(c) for c in group)) or "1"
        if not calendar_enabled:
            branch = date_range.match_branch(
                latest, native, joins, native_keys, scope, partition
            )
        else:
            join = (
                f"INNER JOIN {q(native)} n ON {joins}"
                if joins
                else f"CROSS JOIN {q(native)} n"
            )
            fields = [
                *native_keys,
                *scope,
                "e.__payload AS __payload",
                "e.__order AS __order",
                f"count() OVER (PARTITION BY {partition}) AS __children",
                f"tupleElement(e.__payload,{payload_ids.index('__calendar_days') + 1}) AS __periods",
                f"e.{q(p.calendar.date_id)} AS __resolved_day",
                f"tupleElement(e.__payload,{payload_ids.index(p.calendar.order_id) + 1}) AS __resolved_order",
            ]
            branch = h.cte(f"SELECT {', '.join(fields)} FROM {q(latest)} e {join}")
        date_range.coverage(latest, original_native, criteria, joins)
        exists_join = (
            " AND ".join(
                (
                    f"e.{q(c)} = n.{q(f'__match_attr_{i}')}"
                    for i, c in enumerate(criteria)
                )
            )
            or "1"
        )
        probe = f"SELECT toUInt8(1) AS __matched FROM {q(native)} LIMIT 1"
        if criteria:
            probe = f"SELECT DISTINCT {', '.join((q(f'__match_attr_{i}') for i in range(len(criteria))))}, toUInt8(1) AS __matched FROM {q(native)}"
        h.assertions.append(
            Check(
                "unmatched_resolution_row",
                f"SELECT e.* FROM {q(latest)} e LEFT JOIN ({probe}) n ON {exists_join} WHERE coalesce(n.__matched,0)=0 LIMIT 20",
            )
        )
        return (branch, latest)
