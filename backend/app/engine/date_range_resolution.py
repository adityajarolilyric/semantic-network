"""Calendar-day expansion and native-period mapping for transaction assignments."""

from .filters import dimension_filter_columns
from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q, quote_literal as lit
from .contracts import Check, digest


def calendar_source_ids(objects, transaction):
    by_id = {o["objectId"]: o for o in objects}
    configs = [
        a["dateRange"]
        for a in (transaction["definition"].get("resolution") or {}).get("axes", [])
        if a.get("dateRange")
    ]
    if transaction["definition"].get("calendarResolution"):
        configs.append(transaction["definition"]["calendarResolution"])
    return list(
        dict.fromkeys(
            by_id[c["dimensionId"]]["definition"]["sourceId"] for c in configs
        )
    )


def strict_date(expression):
    text = f"toString({expression})"
    parsed = f"toDate32OrNull({text})"
    return f"if(match({text},'^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') AND toString({parsed})={text},{parsed},NULL)"


class DateRangePlanner:
    def __init__(self, planner):
        self.p = planner
        self.axis = next(
            (a for a in planner.config["axes"] if a.get("dateRange")), None
        )
        self.config = self.axis["dateRange"] if self.axis else None
        self.order_id = (
            "txorder_" + digest([planner.tx["objectId"], "calendar-order"])[:24]
        )
        self.date_id = "txdate_" + digest([planner.tx["objectId"], "resolved-day"])[:24]
        self.required_columns = (
            [self.config[k] for k in ("startColumnId", "endColumnId")]
            if self.config
            else []
        )

    def validate(self):
        if not self.config:
            return
        d = self.p.objects.get(self.config["dimensionId"])
        if (
            not d
            or d["kind"] != "dimension"
            or not d["isTimeDim"]
            or d["definition"]["time"]["baseGrain"] != "day"
        ):
            raise MetadataError(
                "daily_calendar_required",
                "Date ranges require a daily Time dimension with a Day-to-period mapping.",
            )
        self.dimension = d
        outputs = self.p.relationships.outputs(self.p.level["objectId"])
        if any(
            outputs[k].is_time
            for k in self.p.level["definition"]["grain"]["keyIds"]
            if k != self.axis["grainColumnId"]
        ):
            raise MetadataError(
                "range_time_key_ambiguous",
                "Date ranges require one complete native time key, such as YearWeek ID. Separate Year/Week grain keys are not supported by a single range mapping.",
            )
        definition = d["definition"]
        cols = {c["columnId"]: c for c in definition["columns"]}
        allowed = (
            set(definition["keyColumnIds"])
            | set(definition["attributeColumnIds"])
            | {definition["time"]["dateColumnId"]}
        )
        if (
            self.config["calendarKeyColumnId"] not in cols
            or self.config["calendarKeyColumnId"] not in allowed
        ):
            raise MetadataError(
                "invalid_calendar_key",
                "Choose an exposed calendar attribute to map to the native time key.",
            )
        coordinate = outputs[self.axis["grainColumnId"]]
        calendar_key = cols[self.config["calendarKeyColumnId"]]
        if coordinate.is_time and not coordinate.tokens & {
            ("dimension", d["objectId"], calendar_key["columnId"]),
            ("source", definition["sourceId"], calendar_key["sourceColumnId"]),
        }:
            raise MetadataError(
                "range_calendar_key_mismatch",
                "The calendar attribute must match the native time key's existing dimension binding or source lineage.",
            )
        for cid in self.required_columns:
            typ = self.p.columns[cid]["physicalType"]
            if typ not in ("Date", "Date32", "String") and "String" not in typ:
                raise MetadataError(
                    "invalid_range_type",
                    "Range endpoints must be Date/Date32 or ISO date strings (YYYY-MM-DD). DateTime requires an explicit calendar timezone and is not supported.",
                )
        source = self.p.objects.get(definition["sourceId"])
        if source:
            date_col = cols[definition["time"]["dateColumnId"]]
            physical = next(
                (
                    c
                    for c in source["definition"]["columns"]
                    if c["columnId"] == date_col["sourceColumnId"]
                ),
                None,
            )
            if (
                physical
                and physical["physicalType"] not in ("Date", "Date32", "String")
                and "String" not in physical["physicalType"]
            ):
                raise MetadataError(
                    "invalid_calendar_date_type",
                    "The daily calendar date must be a Date/Date32 or ISO date string.",
                )


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


class RangeMeasureAggregator:
    """Collapse daily contributions with Time policy; optional series follow aggregation order."""

    def __init__(self, host, transaction, resolved, measure):
        self.h, self.tx, self.resolved, self.measure = (
            host,
            transaction,
            resolved,
            measure,
        )

    @staticmethod
    def mixed_expression(method):
        from .value_states import mixed_group

        return mixed_group(
            method,
            "range_input.__value",
            "if(range_input.__mixed,2,1)",
            "range_input.__resolved_order",
        )

    def compile_calendar(self, config):
        from .measure_compiler import VALUE

        h, d = self.h, self.measure["definition"]
        keys = h.keys(d["nativePlanningLevelId"])
        date_id = next(
            c["columnId"]
            for c in self.resolved.columns
            if c.get("role") == "resolvedDate"
        )
        order_id = next(
            c["columnId"]
            for c in self.resolved.columns
            if c.get("role") == "resolvedDateOrdering"
        )
        direct = self.tx["definition"].get("mappingMode") != "resolve_multi_grain"
        series = ["__calendar_record"] if direct else config["partitionColumnIds"]
        selected = [
            q(m["sourceColumnId"]) + " AS " + q(m["grainColumnId"])
            for m in self.resolved.mappings
        ]
        selected += [q(cid) for cid in series]
        selected += [
            q(date_id) + " AS __resolved_date",
            q(order_id) + " AS __resolved_order",
            h.widen(q(d["valueColumnId"]), self.measure) + " AS " + q(VALUE),
            "toUInt8(0) AS __mixed",
        ]
        node = h.cte(f"SELECT {', '.join(selected)} FROM {q(self.resolved.node)}")
        policy = d["aggregation"]
        if policy["time"] in ("first", "last"):
            group = ", ".join(q(k) for k in keys)
            h.assertions.append(
                Check(
                    "ambiguous_resolution_time_order",
                    f"SELECT {group}, __resolved_order FROM {q(node)} AS range_input GROUP BY {group}, __resolved_order HAVING uniqExact(__resolved_date)>1 LIMIT 20",
                )
            )
        pending = {"time": True, "nonTime": bool(series)}
        for stage in (
            ["nonTime", "time"]
            if policy.get("order", "nonTimeThenTime") == "nonTimeThenTime"
            else ["time", "nonTime"]
        ):
            if not pending[stage]:
                continue
            pending[stage] = False
            groups = [
                *keys,
                *(["__resolved_date"] if pending["time"] else []),
                *(series if pending["nonTime"] else []),
            ]
            expr = h.aggregate(
                "range_input." + q(VALUE),
                policy[stage],
                policy,
                q("__resolved_order") if stage == "time" else None,
                "if(range_input.__mixed,2,1)",
            )
            ordering = (
                ", min(__resolved_order) AS __resolved_order" if pending["time"] else ""
            )
            mixed = self.mixed_expression(policy[stage])
            group = ", ".join(q(k) for k in groups)
            node = h.cte(
                f"SELECT {group}, if({mixed},NULL,{expr}) AS {q(VALUE)}, toUInt8({mixed}) AS __mixed{ordering} FROM {q(node)} AS range_input GROUP BY {group}"
            )
        return h.cte(f"SELECT *, toUInt8(1) AS __present FROM {q(node)}")

    def compile(self):
        h, d = self.h, self.measure["definition"]
        calendar = self.tx["definition"].get("calendarResolution")
        config = self.tx["definition"].get("resolution") or {
            "axes": [],
            "partitionColumnIds": [],
        }
        axis = next((a for a in config["axes"] if a.get("dateRange")), None)
        if not axis and not calendar:
            return None
        if calendar:
            return self.compile_calendar(config)
        dim = h.objects[axis["dateRange"]["dimensionId"]]["definition"]
        calendar_key = axis["dateRange"]["calendarKeyColumnId"]
        if calendar_key == dim["time"]["dateColumnId"] or set(dim["keyColumnIds"]) <= {
            calendar_key
        }:
            return None  # Native daily identity: retain the normal transaction aggregation.
        from .measure_compiler import VALUE

        date_id = next(
            c["columnId"]
            for c in self.resolved.columns
            if c.get("role") == "resolvedDate"
        )
        selected = [
            q(m["sourceColumnId"]) + " AS " + q(m["grainColumnId"])
            for m in self.resolved.mappings
        ]
        series = [
            "__series_" + str(i) for i in range(len(config["partitionColumnIds"]))
        ]
        selected += [
            q(cid) + " AS " + q(alias)
            for cid, alias in zip(config["partitionColumnIds"], series)
        ]
        selected += [
            q(date_id) + " AS __resolved_date",
            q(
                next(
                    c["columnId"]
                    for c in self.resolved.columns
                    if c.get("role") == "resolvedDateOrdering"
                )
            )
            + " AS __resolved_order",
            h.widen(q(d["valueColumnId"]), self.measure) + " AS " + q(VALUE),
            "toUInt8(0) AS __mixed",
        ]
        node = h.cte(f"SELECT {', '.join(selected)} FROM {q(self.resolved.node)}")
        policy = d["aggregation"]
        if policy["time"] in ("first", "last"):
            group = ", ".join(q(k) for k in h.keys(d["nativePlanningLevelId"]))
            h.assertions.append(
                Check(
                    "ambiguous_resolution_time_order",
                    f"SELECT {group}, __resolved_order FROM {q(node)} AS range_input GROUP BY {group}, __resolved_order HAVING uniqExact(__resolved_date)>1 LIMIT 20",
                )
            )
        pending = {"nonTime": bool(series), "time": True}
        stages = (
            ["nonTime", "time"]
            if policy.get("order", "nonTimeThenTime") == "nonTimeThenTime"
            else ["time", "nonTime"]
        )
        keys = h.keys(d["nativePlanningLevelId"])
        for stage in stages:
            if not pending[stage]:
                continue
            pending[stage] = False
            groups = [
                *keys,
                *(["__resolved_date"] if pending["time"] else []),
                *(series if pending["nonTime"] else []),
            ]
            expression = h.aggregate(
                "range_input." + q(VALUE),
                policy[stage],
                policy,
                q("__resolved_order") if stage == "time" else None,
                "if(range_input.__mixed,2,1)",
            )
            ordering = (
                ", min(__resolved_order) AS __resolved_order" if pending["time"] else ""
            )
            mixed = self.mixed_expression(policy[stage])
            node = h.cte(
                f"SELECT {', '.join(q(k) for k in groups)}, if({mixed},NULL,{expression}) AS {q(VALUE)}, toUInt8({mixed}) AS __mixed{ordering} FROM {q(node)} AS range_input GROUP BY {', '.join(q(k) for k in groups)}"
            )
        return h.cte(f"SELECT *, toUInt8(1) AS __present FROM {q(node)}")
