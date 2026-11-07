"""Read pre-simplification count definitions without changing historical records."""

import json

from .expressions import contains_counts


def source_text(node):
    """Canonical migration source uses resolved IDs, not renameable labels."""
    kind = node["type"]
    if kind == "literal":
        return json.dumps(node["value"], ensure_ascii=False)
    if kind == "measureRef":
        return "[" + node["measureId"] + "]"
    if kind == "attributeRef":
        return (
            "["
            + (
                node.get("memberCatalogId")
                or node.get("dimensionId")
                or node["transactionId"]
            )
            + "."
            + node["columnId"]
            + "]"
        )
    if kind == "binary":
        operator = {
            "add": "+",
            "subtract": "-",
            "multiply": "*",
            "divide": "/",
            "eq": "=",
            "ne": "!=",
            "gt": ">",
            "gte": ">=",
            "lt": "<",
            "lte": "<=",
        }[node["operator"]]
        return (
            "("
            + source_text(node["left"])
            + " "
            + operator
            + " "
            + source_text(node["right"])
            + ")"
        )
    return (
        node["function"].upper()
        + "("
        + ", ".join(source_text(arg) for arg in node["arguments"])
        + ")"
    )


def normalize_count_definition(definition):
    if "countingDimensionId" not in definition:
        return definition
    result = dict(definition)
    counting = result.pop("countingDimensionId")
    expression = (result.get("formula") or {}).get("expression")
    if not counting or not contains_counts(expression):
        return result
    previous = (
        result.get("nativeGrainId")
        or result.get("nativeDimensionId")
        or (result.get("outputGrain") or {}).get("objectId")
    )
    result["nativeGrainId"] = counting
    if "nativeDimensionId" in result:
        result["nativeDimensionId"] = counting
    if previous == counting:
        return result

    def qualify_grains(node, inside_count=False):
        if node["type"] == "grainRef":
            return {
                "type": "attributeRef",
                "dimensionId": counting if inside_count else previous,
                "columnId": node["grainColumnId"],
            }
        if node["type"] == "binary":
            return {
                **node,
                "left": qualify_grains(node["left"], inside_count),
                "right": qualify_grains(node["right"], inside_count),
            }
        if node["type"] in {"call", "count"}:
            return {
                **node,
                "arguments": [
                    qualify_grains(arg, inside_count or node["type"] == "count")
                    for arg in node["arguments"]
                ],
            }
        return node

    expression = qualify_grains(expression)
    result["formula"] = {
        **result["formula"],
        "expression": expression,
        "displayText": source_text(expression),
    }
    return result
