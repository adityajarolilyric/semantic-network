"""Resolve inclusive source ranges against an explicit daily calendar before mapping."""

from ....core.errors import MetadataError
from ....warehouse.sql import quote_identifier as q
from ...sql.contracts import Check, digest
from ..filters import dimension_filter_columns
from ..inputs import input_columns
from .inputs import strict_date


def effective_calendar_objects(objects, managed):
    return [
        {
            **o,
            "definition": {
                **o["definition"],
                "columns": managed[o["objectId"]]["columns"],
            },
        }
        if o["kind"] == "source" and o["objectId"] in managed
        else o
        for o in objects
    ]


class CalendarResolutionPlanner:
    def __init__(self, objects, transaction, source_columns):
        self.objects = {o["objectId"]: o for o in objects}
        self.tx = transaction
        self.config = transaction["definition"].get("calendarResolution")

        self.source_columns = input_columns(transaction, source_columns)
        self.date_id = (
            "txdate_" + digest([transaction["objectId"], "resolved-day"])[:24]
        )
        self.order_id = (
            "txorder_" + digest([transaction["objectId"], "calendar-order"])[:24]
        )
        if not self.config:
            return
        cfg = self.config
        dim = self.objects.get(cfg["memberCatalogId"])
        if (
            not dim
            or dim["kind"] != "dimensionMembers"
            or not dim["isTimeDim"]
            or dim["definition"]["time"]["baseGrain"] != "day"
        ):
            raise MetadataError(
                "daily_calendar_required",
                "Choose a daily Time dimension for date resolution.",
            )
        self.dimension = dim
        d = dim["definition"]
        cols = {c["columnId"]: c for c in d["columns"]}
        allowed = (
            set(d["keyColumnIds"])
            | set(d["attributeColumnIds"])
            | {d["time"]["dateColumnId"]}
        )
        if not set(cfg["selectedColumnIds"]) <= allowed:
            raise MetadataError(
                "invalid_calendar_key", "Choose exposed calendar attributes."
            )
        source = {c["columnId"]: c for c in self.source_columns}
        for cid in (cfg["startColumnId"], cfg["endColumnId"]):
            if cid not in source:
                raise MetadataError(
                    "invalid_resolution_column",
                    "A date-range endpoint is missing from the managed table.",
                )
            self.validate_date_type(source[cid]["physicalType"])
        self.calendar_source = input_columns(
            dim, self.objects[d["sourceId"]]["definition"]["columns"]
        )
        physical = {c["columnId"]: c for c in self.calendar_source}
        self.validate_date_type(
            physical[cols[d["time"]["dateColumnId"]]["sourceColumnId"]]["physicalType"]
        )

    @staticmethod
    def validate_date_type(typ):
        if typ not in ("Date", "Date32", "String") and "String" not in typ:
            raise MetadataError(
                "invalid_range_type",
                "Use Date/Date32 or ISO date strings (YYYY-MM-DD).",
            )

    def column_id(self, cid):
        return (
            "txcal_"
            + digest([self.tx["objectId"], self.config["memberCatalogId"], cid])[:24]
        )

    def added_columns(self):
        d = self.dimension["definition"]
        physical = {c["columnId"]: c for c in self.calendar_source}
        used = {c["name"] for c in self.source_columns}
        result = []
        for col in d["columns"]:
            if col["columnId"] not in self.config["selectedColumnIds"]:
                continue
            source = physical[col["sourceColumnId"]]
            name = source["name"]
            if name in used:
                name = "Calendar_" + name
            suffix, base = 2, name
            while name in used:
                name = base + "_" + str(suffix)
                suffix += 1
            used.add(name)
            result.append(
                {
                    **source,
                    "columnId": self.column_id(col["columnId"]),
                    "name": name,
                    "memberCatalogId": self.dimension["objectId"],
                    "memberColumnId": col["columnId"],
                    "role": "calendarAttribute",
                }
            )
        ordering = next(
            c for c in d["columns"] if c["columnId"] == d["time"]["orderingColumnId"]
        )
        for cid, base, typ, role in (
            (self.date_id, "Resolved date", "Date32", "resolvedDate"),
            (
                self.order_id,
                "Calendar order",
                physical[ordering["sourceColumnId"]]["physicalType"],
                "resolvedDateOrdering",
            ),
        ):
            name = base
            while name in used:
                name += "_"
            used.add(name)
            result.append(
                {
                    "columnId": cid,
                    "name": name,
                    "physicalType": typ,
                    "nullable": False,
                    "role": role,
                }
            )
        return result

    def schema(self):
        return (
            [*self.source_columns, *self.added_columns()]
            if self.config
            else self.source_columns
        )


class CalendarResolutionCompiler:
    def __init__(self, host, transaction):
        self.h, self.tx = host, transaction
        self.source = host.managed[transaction["definition"]["sourceId"]]
        self.p = CalendarResolutionPlanner(
            effective_calendar_objects(list(host.objects.values()), host.managed),
            transaction,
            self.source["columns"],
        )

    def compile(self):
        h, p = self.h, self.p
        cfg, dim = p.config, p.dimension["definition"]
        source = h.managed[dim["sourceId"]]
        dim_object = h.objects[p.dimension["objectId"]]
        calendar_table, calendar_columns = h.object_input(dim_object, source)
        physical = {c["columnId"]: c for c in calendar_columns}
        fields = ", ".join(
            q(physical[c["sourceColumnId"]]["name"]) + " AS " + q(c["columnId"])
            for c in dim["columns"]
        )
        sql = f"SELECT {fields} FROM {calendar_table}"
        if dim.get("filters"):
            sql = f"SELECT * FROM ({sql}) WHERE {h.filter_sql(dim['filters'], dimension_filter_columns(dim, calendar_columns))}"
        selected = [
            strict_date(q(dim["time"]["dateColumnId"])) + " AS " + q(p.date_id),
            q(dim["time"]["orderingColumnId"]) + " AS " + q(p.order_id),
        ]
        selected += [
            q(cid) + " AS " + q(p.column_id(cid)) for cid in cfg["selectedColumnIds"]
        ]
        calendar = h.cte(f"SELECT DISTINCT {', '.join(selected)} FROM ({sql})")
        day = q(p.date_id)
        attrs = ", ".join(
            q(c["columnId"]) for c in p.added_columns() if c["columnId"] != p.date_id
        )
        h.assertions += [
            Check(
                "invalid_resolution_calendar",
                f"SELECT * FROM {q(calendar)} WHERE isNull({day}) OR isNull({q(p.order_id)}) LIMIT 20",
            ),
            Check(
                "ambiguous_resolution_calendar",
                f"SELECT {day} FROM {q(calendar)} GROUP BY {day} HAVING uniqExact(tuple({attrs}))>1 LIMIT 20",
            ),
        ]
        input_table, _ = h.object_input(self.tx, self.source)
        rawfields = ", ".join(
            q(c["name"]) + " AS " + q(c["columnId"]) for c in p.source_columns
        )
        raw = h.cte(
            f"SELECT {rawfields}, rowNumberInAllBlocks() AS __calendar_record FROM {input_table}"
        )
        bounds = h.cte(
            f"SELECT *, {strict_date(q(cfg['startColumnId']))} AS __range_start, {strict_date(q(cfg['endColumnId']))} AS __range_end FROM {q(raw)}"
        )
        h.assertions += [
            Check(
                "invalid_resolution_range",
                f"SELECT * FROM {q(bounds)} WHERE isNull(__range_start) OR isNull(__range_end) OR __range_start>__range_end LIMIT 20",
            ),
            Check(
                "resolution_calendar_coverage",
                f"SELECT * FROM {q(bounds)} WHERE (SELECT count() FROM {q(calendar)})=0 OR __range_start<(SELECT min({day}) FROM {q(calendar)}) OR __range_end>(SELECT max({day}) FROM {q(calendar)}) LIMIT 20",
            ),
        ]
        eligible = h.cte(
            f"SELECT DISTINCT e.__range_start, e.__range_end, toUInt8(1) AS __present FROM {q(bounds)} e CROSS JOIN {q(calendar)} d WHERE d.{day}>=e.__range_start AND d.{day}<=e.__range_end"
        )
        h.assertions.append(
            Check(
                "unmatched_resolution_range",
                f"SELECT e.__range_start,e.__range_end FROM {q(bounds)} e LEFT JOIN {q(eligible)} d ON e.__range_start=d.__range_start AND e.__range_end=d.__range_end WHERE coalesce(d.__present,0)=0 LIMIT 20",
            )
        )
        additions = ", ".join(
            "d." + q(c["columnId"]) + " AS " + q(c["columnId"])
            for c in p.added_columns()
        )
        node = h.cte(
            f"SELECT e.*, {additions}, count() OVER (PARTITION BY e.__calendar_record) AS __calendar_days FROM {q(bounds)} e CROSS JOIN {q(calendar)} d WHERE d.{day}>=e.__range_start AND d.{day}<=e.__range_end"
        )
        h.assertions.append(
            Check(
                "resolution_expansion_limit",
                f"SELECT count() AS expanded FROM {q(node)} HAVING expanded>{h.resolution_max_rows}",
            )
        )
        return node
