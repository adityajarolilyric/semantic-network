from ....warehouse.sql import quote_identifier as q
from ...sql.contracts import Check
from ..dates.calendar import CalendarResolutionCompiler
from ..dates.ranges import DateRangeCompiler
from .contracts import VALUE_NAME, PreparedResolutionInput


class ResolutionInputCompiler:
    """Prepare transformed inputs, profile guards and the native population seed."""

    def __init__(self, resolution):
        self.resolution = resolution

    def compile(self):
        h, p, config = (
            self.resolution.host,
            self.resolution.planner,
            self.resolution.config,
        )
        source_table, _ = h.object_input(self.resolution.tx, self.resolution.source)
        calendar_enabled = bool(p.calendar.config)
        if calendar_enabled:
            expanded = CalendarResolutionCompiler(h, self.resolution.tx).compile()
            source_table = (
                "("
                + "SELECT "
                + ", ".join(
                    (q(cid) + " AS " + q(c["name"]) for cid, c in p.columns.items())
                )
                + ", __calendar_days FROM "
                + q(expanded)
                + ")"
            )
        raw = h.cte(
            "SELECT "
            + ", ".join(
                (q(c["name"]) + " AS " + q(cid) for cid, c in p.columns.items())
            )
            + (", __calendar_days" if calendar_enabled else "")
            + " FROM "
            + source_table
        )
        order_ids = config["orderColumnIds"]
        partitions = config["partitionColumnIds"]
        payload_ids = list(
            dict.fromkeys(
                [
                    *(v["sourceColumnId"] for v in config["values"]),
                    *order_ids,
                    *partitions,
                    *config["passthroughColumnIds"],
                ]
            )
        )
        if calendar_enabled:
            payload_ids = list(
                dict.fromkeys(
                    [
                        *payload_ids,
                        p.calendar.date_id,
                        p.calendar.order_id,
                        "__calendar_days",
                    ]
                )
            )
        payload = "tuple(" + ", ".join((q(cid) for cid in payload_ids)) + ")"
        order = "tuple(" + ", ".join((q(cid) for cid in order_ids)) + ")"
        h.assertions.append(
            Check(
                "null_resolution_order",
                f"SELECT * FROM {q(raw)} WHERE {' OR '.join(('isNull(' + q(cid) + ')' for cid in order_ids))} LIMIT 20",
            )
        )
        flag_expressions = p.flags()
        flag_sql = (
            "tuple(" + ", ".join(flag_expressions) + ")"
            if flag_expressions
            else "tuple(0)"
        )
        flagged = h.cte(
            f"SELECT {', '.join((q(c['name']) + ' AS ' + q(cid) for cid, c in p.columns.items()))}{(', __calendar_days' if calendar_enabled else '')}, {flag_sql} AS __profile FROM {source_table}"
        )
        known_profiles = ", ".join(
            (
                "tuple(" + ", ".join((str(v) for v in profile)) + ")"
                for profile in self.resolution.profiles
            )
        )
        h.assertions.append(
            Check(
                "resolution_profiles_changed",
                f"SELECT * FROM {q(flagged)} WHERE "
                + (f"__profile NOT IN ({known_profiles})" if known_profiles else "1")
                + " LIMIT 20",
            )
        )
        seed = h.cte(
            f"SELECT {', '.join((q(k) for k in h.keys(self.resolution.level)))}, 0 AS {q(VALUE_NAME)} FROM ({h.population(self.resolution.level)})"
        )
        date_range = DateRangeCompiler(self.resolution)
        date_range.prepare(raw)
        return PreparedResolutionInput(
            raw,
            flagged,
            seed,
            payload_ids,
            order_ids,
            partitions,
            payload,
            order,
            calendar_enabled,
            date_range,
        )
