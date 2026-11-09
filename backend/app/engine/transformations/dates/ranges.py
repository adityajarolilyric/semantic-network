"""Calendar-day expansion and native-period mapping for transaction assignments."""

from ....warehouse.sql import quote_identifier as q
from ....warehouse.sql import quote_literal as lit
from ...sql.contracts import Check
from ..filters import dimension_filter_columns
from .inputs import strict_date


class DateRangeCompiler:
    def __init__(self, compiler):
        self.c = compiler
        self.h = compiler.host
        self.p = compiler.planner.date_range
        self.enabled = bool(self.p.config)
        self.bound_names = ["__range_start", "__range_end"] if self.enabled else []

    def prepare(self, raw):
        if not self.enabled:
            return
        h, cfg = self.h, self.p.config
        dim = self.p.dimension["definition"]
        source = h.managed[dim["sourceId"]]
        input_table, columns = h.object_input(self.p.dimension, source)
        source_cols = {c["columnId"]: c for c in columns}
        fields = ", ".join(
            q(source_cols[c["sourceColumnId"]]["name"]) + " AS " + q(c["columnId"])
            for c in dim["columns"]
        )
        dim_sql = f"SELECT {fields} FROM {input_table}"
        if dim.get("filters"):
            dim_sql = f"SELECT * FROM ({dim_sql}) WHERE {h.filter_sql(dim['filters'], dimension_filter_columns(dim, columns))}"
        date = strict_date(q(dim["time"]["dateColumnId"]))
        key = q(cfg["calendarKeyColumnId"])
        typ = next(
            c["physicalType"]
            for c in h.populations[self.c.level]["columns"]
            if c["columnId"] == self.p.axis["grainColumnId"]
        )
        period = (
            f"toString({key})"
            if "String" in typ
            else f"accurateCastOrNull(toString({key}),{lit(typ)})"
        )
        self.calendar = h.cte(
            f"SELECT DISTINCT {date} AS __resolved_day, {period} AS __period_key, {q(dim['time']['orderingColumnId'])} AS __resolved_order FROM ({dim_sql})"
        )
        h.assertions.append(
            Check(
                "invalid_resolution_calendar",
                f"SELECT * FROM {q(self.calendar)} WHERE isNull(__resolved_day) OR isNull(__period_key) OR isNull(__resolved_order) LIMIT 20",
            )
        )
        h.assertions.append(
            Check(
                "ambiguous_resolution_calendar",
                f"SELECT __resolved_day FROM {q(self.calendar)} GROUP BY __resolved_day HAVING uniqExact(tuple(__period_key,__resolved_order))>1 LIMIT 20",
            )
        )
        bounds = h.cte(f"SELECT *, {', '.join(self.bounds())} FROM {q(raw)}")
        h.assertions.append(
            Check(
                "invalid_resolution_range",
                f"SELECT * FROM {q(bounds)} WHERE isNull(__range_start) OR isNull(__range_end) OR __range_start>__range_end LIMIT 20",
            )
        )
        h.assertions.append(
            Check(
                "resolution_calendar_coverage",
                f"SELECT * FROM {q(bounds)} WHERE (SELECT count() FROM {q(self.calendar)})=0 OR __range_start<(SELECT min(__resolved_day) FROM {q(self.calendar)}) OR __range_end>(SELECT max(__resolved_day) FROM {q(self.calendar)}) LIMIT 20",
            )
        )

    def bounds(self):
        if not self.enabled:
            return []
        return [
            strict_date(q(self.p.config[k])) + " AS " + q(alias)
            for k, alias in zip(("startColumnId", "endColumnId"), self.bound_names)
        ]

    def expand_native(self, native):
        if not self.enabled:
            return native
        key = q(self.p.axis["grainColumnId"])
        return self.h.cte(
            f"SELECT n.*, d.__resolved_day AS __resolved_day, d.__resolved_order AS __resolved_order FROM {q(native)} n INNER JOIN {q(self.calendar)} d ON n.{key}=d.__period_key"
        )

    def match_branch(self, latest, native, joins, native_keys, scope, partition):
        selected = [
            *native_keys,
            *scope,
            "e.__payload AS __payload",
            "e.__order AS __order",
        ]
        where = ""
        if self.enabled:
            non_time = [
                k
                for k in self.h.keys(self.c.level)
                if k != self.p.axis["grainColumnId"]
            ]
            member = (
                "tuple(" + ", ".join("n." + q(k) for k in non_time) + ")"
                if non_time
                else "tuple(1)"
            )
            per_member = partition + (
                ", " + ", ".join("n." + q(k) for k in non_time) if non_time else ""
            )
            selected += [
                f"uniqExact({member}) OVER (PARTITION BY {partition}) AS __children",
                f"count() OVER (PARTITION BY {per_member}) AS __periods",
                "n.__resolved_day AS __resolved_day",
                "n.__resolved_order AS __resolved_order",
            ]
            where = " WHERE n.__resolved_day>=e.__range_start AND n.__resolved_day<=e.__range_end"
        else:
            selected += [
                f"count() OVER (PARTITION BY {partition}) AS __children",
                "toUInt64(1) AS __periods",
                "toUInt8(0) AS __resolved_order",
            ]
        join = (
            f"INNER JOIN {q(native)} n ON {joins}"
            if joins
            else f"CROSS JOIN {q(native)} n"
        )
        return self.h.cte(
            f"SELECT {', '.join(selected)} FROM {q(latest)} e {join}{where}"
        )

    def coverage(self, latest, native, criteria, joins):
        if not self.enabled:
            return
        h = self.h
        time_key = self.p.axis["grainColumnId"]
        non_time = [k for k in h.keys(self.c.level) if k != time_key]
        attrs = [f"__match_attr_{i}" for i in range(len(criteria))]
        projection = [*non_time, *attrs]
        members = h.cte(
            "SELECT DISTINCT "
            + (", ".join(q(k) for k in projection) if projection else "1 AS __member")
            + " FROM "
            + q(native)
        )
        join = (
            f"INNER JOIN {q(members)} n ON {joins}"
            if joins
            else f"CROSS JOIN {q(members)} n"
        )
        expected = h.cte(
            f"SELECT {', '.join(['n.' + q(k) + ' AS ' + q(k) for k in non_time] + ['d.__period_key AS ' + q(time_key), 'd.__resolved_day AS __resolved_day'])} FROM {q(latest)} e {join} CROSS JOIN {q(self.calendar)} d WHERE d.__resolved_day>=e.__range_start AND d.__resolved_day<=e.__range_end"
        )
        key_join = " AND ".join(
            "e." + q(k) + "=p." + q(k) for k in h.keys(self.c.level)
        )
        h.assertions.append(
            Check(
                "resolution_native_coverage",
                f"SELECT e.* FROM {q(expected)} e LEFT JOIN (SELECT *, toUInt8(1) AS __present FROM ({h.population(self.c.level)})) p ON {key_join} WHERE coalesce(p.__present,0)=0 LIMIT 20",
            )
        )
        ranges = h.cte(f"SELECT DISTINCT __range_start,__range_end FROM {q(latest)}")
        covered = h.cte(
            f"SELECT e.__range_start,e.__range_end,toUInt8(1) AS __present FROM {q(ranges)} e CROSS JOIN {q(self.calendar)} d WHERE d.__resolved_day>=e.__range_start AND d.__resolved_day<=e.__range_end GROUP BY e.__range_start,e.__range_end"
        )
        h.assertions.append(
            Check(
                "unmatched_resolution_range",
                f"SELECT e.* FROM {q(ranges)} e LEFT JOIN {q(covered)} d ON e.__range_start=d.__range_start AND e.__range_end=d.__range_end WHERE coalesce(d.__present,0)=0 LIMIT 20",
            )
        )
