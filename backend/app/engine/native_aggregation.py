"""Aggregate compatible direct Transaction columns in one native-grain scan."""

from ..warehouse.sql import quote_identifier as q


def direct_transaction_group(compiler, measure, table, columns, mappings):
    transaction_id = measure["definition"]["transactionId"]
    if transaction_id not in compiler.native_groups:
        selected = [
            q(columns[m["sourceColumnId"]]["name"]) + " AS " + q(m["grainColumnId"])
            for m in mappings
        ]
        outputs = {}
        for mid, candidate in compiler.measures.items():
            d = candidate["definition"]
            if d["measureType"] != "referenced" or d["transactionId"] != transaction_id:
                continue
            if (
                compiler.native_workset is not None
                and mid not in compiler.native_workset
                and mid != measure["objectId"]
            ):
                continue
            value = compiler.widen(q(columns[d["valueColumnId"]]["name"]), candidate)
            name, mixed = (
                "__native_value_" + str(len(outputs)),
                "__native_mixed_" + str(len(outputs)),
            )
            selected.append(
                compiler.aggregate(value, d["aggregation"]["nonTime"], d["aggregation"])
                + " AS "
                + q(name)
            )
            selected.append(
                (
                    f"toUInt8(uniqExact({value})>1)"
                    if d["resultType"]["type"] in {"string", "date", "boolean"}
                    and d["aggregation"]["nonTime"] == "single_value"
                    else "toUInt8(0)"
                )
                + " AS "
                + q(mixed)
            )
            outputs[mid] = (name, mixed)
        selected.append("toUInt8(1) AS __present")
        group = ", ".join(q(columns[m["sourceColumnId"]]["name"]) for m in mappings)
        compiler.native_groups[transaction_id] = (
            compiler.cte(f"SELECT {', '.join(selected)} FROM {table} GROUP BY {group}"),
            outputs,
        )
    node, outputs = compiler.native_groups[transaction_id]
    value, mixed = outputs[measure["objectId"]]
    return node, value, mixed
