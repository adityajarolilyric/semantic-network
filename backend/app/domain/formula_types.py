"""Type inference and function signatures for the semantic expression AST."""

import re
from datetime import date

from ..core.errors import MetadataError
from ..formula_language.catalog import FUNCTIONS
from .grains import transaction_column_reference
from .typed_values import NUMERIC, result_type


def infer(node, measure_type, attribute_type, depth=0):
    def error(message):
        raise MetadataError("formula_type", message)

    if depth > 32:
        error("Formula nesting exceeds 32 levels.")
    kind = node["type"]
    if kind == "literal":
        value = node["value"]
        return (
            "boolean"
            if isinstance(value, bool)
            else "string"
            if isinstance(value, str)
            else "number"
        )
    if kind == "count":
        args = node["arguments"]
        if node["function"] == "count_rows":
            if args:
                error("COUNT_ROWS takes no arguments.")
        else:
            if len(args) != 1:
                error("Count functions require exactly one argument.")
            argument_type = infer(args[0], measure_type, attribute_type, depth + 1)
            if node["function"] == "count_if" and argument_type != "boolean":
                error("COUNT_IF requires a Boolean condition.")
        return "number"
    if kind == "measureRef":
        value = measure_type(node["measureId"])
        return "number" if value in NUMERIC else value
    if kind in {"attributeRef", "grainRef"}:
        value = attribute_type(node)
        return "number" if value in NUMERIC else value

    def child(n):
        value = infer(n, measure_type, attribute_type, depth + 1)
        return "number" if value in NUMERIC else value

    if kind == "binary":
        left, right = child(node["left"]), child(node["right"])
        if node["operator"] in {"eq", "ne", "gt", "gte", "lt", "lte"}:
            if left != right:
                error(
                    "Comparison operands must have matching types; use explicit conversion."
                )
            if left == "boolean" and node["operator"] not in {"eq", "ne"}:
                error("Boolean values support equals and not equals comparisons only.")
            return "boolean"
        if left != "number" or right != "number":
            error(
                "Arithmetic requires numeric inputs. Use ADD_DAYS or DAYS_BETWEEN for dates."
            )
        return "number"
    fn = node["function"]
    args = [child(n) for n in node["arguments"]]
    low, high = FUNCTIONS[fn]
    if not low <= len(args) <= high:
        raise MetadataError("function_arity", f"Invalid argument count for {fn}.")
    if fn == "if":
        if args[0] != "boolean" or args[1] != args[2]:
            error("IF requires a Boolean condition and matching result branch types.")
        return args[1]
    if fn in {"and", "or", "not"}:
        if any(t != "boolean" for t in args):
            error("Logical functions require Boolean inputs.")
        return "boolean"
    if fn == "is_null":
        return "boolean"
    if fn in {"concat", "upper", "lower", "trim"}:
        if any(t != "string" for t in args):
            error("String functions require String inputs.")
        return "string"
    if fn == "date":
        if args != ["string"]:
            error("DATE requires an ISO date string.")

        literal = node["arguments"][0]
        if literal["type"] == "literal":
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", literal["value"]):
                    raise ValueError()
                date.fromisoformat(literal["value"])
            except ValueError:
                error("DATE requires a valid ISO date string (YYYY-MM-DD).")
        return "date"
    if fn == "add_days":
        if args != ["date", "number"]:
            error("ADD_DAYS requires a Date and a numeric day count.")
        return "date"
    if fn == "days_between":
        if args != ["date", "date"]:
            error("DAYS_BETWEEN requires two Dates.")
        return "number"
    if fn in {"abs", "round"} and any(t != "number" for t in args):
        error("ABS and ROUND require numeric inputs.")
    if len(set(args)) > 1:
        error("Function arguments must have matching types.")
    return args[0]


def attribute_physical_type(context, owner, node):
    if node["type"] == "grainRef":
        level = context.objects.get(owner["definition"]["nativeGrainId"])
        physical = context.grain_type(level, node["grainColumnId"]) if level else None
    elif node.get("memberCatalogId"):
        obj = context.objects.get(node["memberCatalogId"])
        physical = (
            context.dimension_type(obj, node["columnId"])
            if obj and obj["kind"] == "dimensionMembers"
            else None
        )
    elif node.get("transactionId"):
        grain_id, column_id = transaction_column_reference(
            node["transactionId"], node["columnId"], list(context.objects.values())
        )
        obj = context.objects.get(grain_id)
        physical = context.grain_type(obj, column_id) if obj and column_id else None
    else:
        obj = context.objects.get(node.get("dimensionId"))
        physical = (
            context.grain_type(obj, node["columnId"])
            if obj and obj["kind"] in {"dimension", "grain"}
            else None
        )
    value = result_type(physical or "", True)
    if not value:
        raise MetadataError(
            "formula_attribute_type",
            "Choose a supported Number, String, Date or Boolean attribute.",
        )
    return value["type"]
