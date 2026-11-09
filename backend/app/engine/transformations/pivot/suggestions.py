"""Conservative, deterministic inference; never saves or executes a pivot."""

import re
from collections import defaultdict

from ....schemas.managed_tables import PivotStage
from ....schemas.pivot_suggestions import PivotSuggestionResult
from ...sql.contracts import digest

ATTRIBUTE_WORDS = {
    "attribute",
    "attr",
    "property",
    "field",
    "characteristic",
    "metric",
    "measure",
}
VALUE_WORDS = {"value", "val", "amount", "text"}


def words(name):
    return (
        re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
        .lower()
        .replace(" ", "_")
        .split("_")
    )


def stem(tokens):
    return tuple(
        token
        for token in tokens
        if token not in ATTRIBUTE_WORDS | VALUE_WORDS | {"id", "name"} and token
    )


class PivotSuggestionDetector:
    max_rows = 100

    def suggest(self, table_id, columns, rows):
        rows = rows[: self.max_rows]
        result = PivotSuggestionResult(
            tableId=table_id,
            rowsExamined=len(rows),
            complete=len(rows) < self.max_rows,
        )
        if not result.complete:
            result.warnings.append(
                "Only the first 100 rows were inspected; other attribute values may exist. Use Discover to load additional values before saving."
            )
        if not rows:
            result.warnings.append("The table has no rows to inspect.")
            return result
        attributes = []
        values = []
        for index, column in enumerate(columns):
            tokens = set(words(column["name"]))
            if tokens & VALUE_WORDS:
                values.append(index)
            elif tokens & ATTRIBUTE_WORDS and "String" in column["physicalType"]:
                attributes.append(index)
        candidates = []
        for attribute in attributes:
            labels = sorted(
                {row[attribute] for row in rows if row[attribute] is not None}
            )
            if (
                not labels
                or len(labels) > 200
                or any(
                    not isinstance(label, str)
                    or not label.strip()
                    or len(label) > 200
                    or any(ord(char) < 32 for char in label)
                    for label in labels
                )
            ):
                continue
            for value in values:
                if not any(
                    row[value] is not None and row[attribute] is not None
                    for row in rows
                ):
                    continue
                matching = stem(words(columns[attribute]["name"])) == stem(
                    words(columns[value]["name"])
                )
                if matching or len(attributes) == len(values) == 1:
                    candidates.append((attribute, value, labels, matching))
        # Never choose arbitrarily when a column has more than one plausible partner.
        pairs = [
            candidate
            for candidate in candidates
            if sum(other[0] == candidate[0] for other in candidates) == 1
            and sum(other[1] == candidate[1] for other in candidates) == 1
        ]
        if not pairs or len(pairs) > 20:
            result.warnings.append(
                "No unambiguous attribute/value pairs were found. Select the columns manually."
            )
            return result
        consumed = {
            index for attribute, value, _, _ in pairs for index in (attribute, value)
        }
        retained = [index for index in range(len(columns)) if index not in consumed]
        if len(retained) > 32:
            result.warnings.append(
                "More than 32 retained columns were found. Select the columns to retain manually."
            )
            return result
        names = {columns[index]["name"] for index in retained}
        proposed = []
        for attribute, value, labels, _ in pairs:
            outputs = []
            for label in labels:
                name = label.strip()
                if name in names:
                    name = (columns[attribute]["name"] + "_" + name)[:190]
                base, suffix = name, 2
                while name in names:
                    name = f"{base[:190]}_{suffix}"
                    suffix += 1
                names.add(name)
                outputs.append({"attributeValue": label, "name": name})
            proposed.append(
                {
                    "pairId": "pair_"
                    + digest(
                        [
                            table_id,
                            columns[attribute]["columnId"],
                            columns[value]["columnId"],
                        ]
                    )[:28],
                    "attributeColumnId": columns[attribute]["columnId"],
                    "valueColumnId": columns[value]["columnId"],
                    "outputs": outputs,
                }
            )
        record_keys = []
        if len(pairs) == 1:
            key_indexes = [
                index
                for index in retained
                if set(words(columns[index]["name"])) & {"id", "key", "code"}
            ]
            if (
                key_indexes
                and len(key_indexes) <= 32
                and all(
                    all(row[index] is not None for index in key_indexes) for row in rows
                )
            ):
                groups = defaultdict(list)
                for row in rows:
                    groups[tuple(row[index] for index in key_indexes)].append(row)
                attribute = pairs[0][0]
                if any(len(group) > 1 for group in groups.values()) and all(
                    len({row[attribute] for row in group}) == len(group)
                    and all(
                        len({row[index] for row in group}) == 1 for index in retained
                    )
                    for group in groups.values()
                ):
                    record_keys = [columns[index]["columnId"] for index in key_indexes]
        result.pivot = PivotStage.model_validate(
            {
                "stageId": "stage_" + digest(["suggested-pivot", table_id])[:28],
                "recordKeyColumnIds": record_keys,
                "groupByColumnIds": [columns[index]["columnId"] for index in retained],
                "pairs": proposed,
            }
        )
        result.confidence = "high" if all(pair[3] for pair in pairs) else "medium"
        result.reasons.append(
            f"Found {len(pairs)} attribute/value pair(s) using matching column names and observed selector values."
        )
        result.reasons.append(
            "Repeated identifier keys consistently describe separate attribute rows."
            if record_keys
            else "Rows are preserved independently; no record grouping was inferred."
        )
        result.warnings.append(
            "Review the suggested pairs, retained columns and record keys before saving. Detection cannot establish business grain."
        )
        return result
