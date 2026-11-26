from ...warehouse.sql import quote_identifier as q
from ..sql.contracts import Check
from .contracts import VALUE
from .value_states import mixed_group


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
        return mixed_group(
            method,
            "range_input.__value",
            "if(range_input.__mixed,2,1)",
            "range_input.__resolved_order",
        )

    def compile_calendar(self, config):
        h, d = self.h, self.measure["definition"]
        keys = h.keys(d["nativeGrainId"])
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
        dim = h.objects[axis["dateRange"]["memberCatalogId"]]["definition"]
        calendar_key = axis["dateRange"]["calendarKeyColumnId"]
        if calendar_key == dim["time"]["dateColumnId"] or set(dim["keyColumnIds"]) <= {
            calendar_key
        }:
            return None  # Native daily identity: retain the normal transaction aggregation.

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
            group = ", ".join(q(k) for k in h.keys(d["nativeGrainId"]))
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
        keys = h.keys(d["nativeGrainId"])
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
