from ....warehouse.sql import quote_identifier as q
from ...sql.contracts import Check
from .contracts import ResolvedTransaction, key_id


class ResolutionOverlayCompiler:
    """Choose the last assignment at every native cell and project resolved values."""

    def __init__(self, resolution):
        self.resolution = resolution

    def compile(self, prepared, branches, latest_nodes):
        h, p, config = (
            self.resolution.host,
            self.resolution.planner,
            self.resolution.config,
        )
        raw = prepared.raw_node
        payload_ids = prepared.payload_column_ids
        order_ids = prepared.order_column_ids
        partitions = prepared.partition_column_ids
        calendar_enabled = prepared.calendar_enabled
        date_range = prepared.date_range
        keys = h.keys(self.resolution.level)
        native_ids = ["__native_" + str(i) for i in range(len(keys))]
        if branches:
            matches = h.cte(
                " UNION ALL ".join(("SELECT * FROM " + q(b) for b in branches))
            )
        else:
            native_selected = [
                "n." + q(k) + " AS " + q(native_ids[i]) for i, k in enumerate(keys)
            ]
            expressions = native_selected + [
                "e." + q(c) + " AS " + q(c) for c in partitions
            ]
            expressions += [
                "tuple("
                + ", ".join(("e." + q(c) for c in payload_ids))
                + ") AS __payload",
                "tuple(" + ", ".join(("e." + q(c) for c in order_ids)) + ") AS __order",
                "toUInt64(1) AS __children",
                "toUInt64(1) AS __periods",
                f"(SELECT any(__resolved_order) FROM {q(date_range.calendar)}) AS __resolved_order"
                if date_range.enabled
                else f"CAST(NULL AS Nullable({next((c['physicalType'] for c in p.calendar.added_columns() if c['columnId'] == p.calendar.order_id))})) AS __resolved_order"
                if calendar_enabled
                else "toUInt8(0) AS __resolved_order",
                *(
                    ["toDate32('1970-01-01') AS __resolved_day"]
                    if date_range.enabled or calendar_enabled
                    else []
                ),
            ]
            matches = h.cte(
                f"SELECT {', '.join(expressions)} FROM ({h.population(self.resolution.level)}) n CROSS JOIN {q(raw)} e WHERE 0"
            )
        group = [
            *native_ids,
            *(["__resolved_day"] if date_range.enabled or calendar_enabled else []),
            *partitions,
        ]
        grouped = ", ".join((q(c) for c in group))
        h.assertions.append(
            Check(
                "resolution_expansion_limit",
                f"SELECT count() AS expanded FROM {q(matches)} HAVING expanded>{self.resolution.max_rows}",
            )
        )
        h.assertions.append(
            Check(
                "ambiguous_resolution_order",
                f"SELECT {grouped}, __order FROM {q(matches)} GROUP BY {grouped}, __order HAVING count()>1 LIMIT 20",
            )
        )
        # Keep NULL inside the winning payload; never fall back to an earlier non-NULL row.
        winner = h.cte(
            f"SELECT {grouped}, argMax(tuple(__payload,__children,__periods,__resolved_order),__order) AS __winner FROM {q(matches)} GROUP BY {grouped}"
        )
        columns = p.schema(
            h.populations[self.resolution.level]["columns"],
            h.managed[p.date_range.dimension["definition"]["sourceId"]]["columns"]
            if date_range.enabled
            else (),
        )
        selected = [
            q(native_ids[i]) + " AS " + q(key_id(self.resolution.tx["objectId"], k))
            for i, k in enumerate(keys)
        ]
        if calendar_enabled:
            selected += [
                q("__resolved_day") + " AS " + q(p.calendar.date_id),
                "tupleElement(__winner,4) AS " + q(p.calendar.order_id),
            ]
        if date_range.enabled:
            selected.append(q("__resolved_day") + " AS " + q(p.date_range.date_id))
            selected.append("tupleElement(__winner,4) AS " + q(p.date_range.order_id))
        modes = {v["sourceColumnId"]: v["mode"] for v in config["values"]}
        time_modes = {v["sourceColumnId"]: v.get("timeMode") for v in config["values"]}
        for cid in payload_ids:
            if calendar_enabled and cid in (
                p.calendar.date_id,
                p.calendar.order_id,
                "__calendar_days",
            ):
                continue
            value = (
                f"tupleElement(tupleElement(__winner,1),{payload_ids.index(cid) + 1})"
            )
            if cid in modes:
                value = f"toFloat64({value})"
                if modes[cid] == "split":
                    value += "/tupleElement(__winner,2)"
                if (date_range.enabled or calendar_enabled) and time_modes[
                    cid
                ] == "split":
                    value += "/tupleElement(__winner,3)"
            selected.append(value + " AS " + q(cid))
        node = h.cte(f"SELECT {', '.join(selected)} FROM {q(winner)}")
        mappings = [
            {
                "sourceColumnId": key_id(self.resolution.tx["objectId"], k),
                "grainColumnId": k,
            }
            for k in keys
        ]
        return ResolvedTransaction(node, columns, mappings, matches, latest_nodes, raw)
