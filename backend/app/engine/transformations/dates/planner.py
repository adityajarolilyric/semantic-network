from ....core.errors import MetadataError
from ...sql.contracts import digest


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
        d = self.p.objects.get(self.config["memberCatalogId"])
        if (
            not d
            or d["kind"] != "dimensionMembers"
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
            ("dimensionMembers", d["objectId"], calendar_key["columnId"]),
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
