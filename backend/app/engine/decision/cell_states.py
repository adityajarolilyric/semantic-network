"""Keep constant numeric cell states in the API, not in warehouse projections."""


def decode_cell_states(rows, measure_ids, schema):
    positions = {
        column["measureId"]: column["resultIndex"]
        for column in schema.columns
        if column.get("role") == "mixed"
    }
    return [
        [
            "mixed"
            if str(row[positions[mid]] if mid in positions else 0).lower()
            in {"1", "true"}
            else None
            for mid in measure_ids
        ]
        for row in rows
    ]
