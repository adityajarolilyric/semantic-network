"""Shared value capabilities; no database or execution state."""

import re
from .measure_policies import referenced_result_type

NUMERIC = {"number", "integer", "decimal"}
METHODS = {
    "number": {"sum", "avg", "min", "max", "count", "count_distinct"},
    "string": {"single_value"},
    "date": {"single_value", "min", "max"},
    "boolean": {"single_value", "any_true", "all_true"},
}


def result_type(physical_type, nullable=True):
    raw = re.sub(r"\s+", "", physical_type or "").lower()
    while raw.startswith(("nullable(", "lowcardinality(")) and raw.endswith(")"):
        raw = raw[raw.index("(") + 1 : -1]
    kind = (
        "string"
        if raw == "string" or raw.startswith("fixedstring(")
        else "boolean"
        if raw in {"bool", "boolean"}
        else "date"
        if raw in {"date", "date32"}
        else None
    )
    return (
        {"type": kind, "nullable": nullable}
        if kind
        else referenced_result_type(physical_type or "", nullable)
    )


def is_numeric(definition):
    return definition["resultType"]["type"] in NUMERIC


def methods(kind, time=False):
    return METHODS["number" if kind in NUMERIC else kind] | (
        {"first", "last"} if time else set()
    )


def uses_typed_features(document):
    """Keep unchanged numeric evaluation fingerprints compatible across this extension."""
    definition = document["definition"]
    if not is_numeric(definition):
        return True

    def visit(node):
        if not isinstance(node, dict):
            return False
        if node.get("type") in {"attributeRef", "count"}:
            return True
        if node.get("type") == "literal" and isinstance(node.get("value"), (str, bool)):
            return True
        if node.get("type") == "binary" and node["operator"] not in {
            "add",
            "subtract",
            "multiply",
            "divide",
        }:
            return True
        if node.get("type") == "call" and node["function"] not in {
            "abs",
            "round",
            "coalesce",
            "min",
            "max",
        }:
            return True
        return any(
            visit(value) for value in node.values() if isinstance(value, dict)
        ) or any(
            visit(value)
            for values in node.values()
            if isinstance(values, list)
            for value in values
        )

    return visit((definition.get("formula") or {}).get("expression"))
