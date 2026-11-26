"""Validated temporal call contracts, shared by SQL and edit-impact planning."""

from ..core.errors import MetadataError
from .catalog import BY_NAME

OPERATIONS = {
    "SUM": "sum",
    "AVG": "avg",
    "MIN": "min",
    "MAX": "max",
    "COUNT": "count",
    "STDDEV_POP": "stddevPop",
    "STDDEV_SAMP": "stddevSamp",
}


def is_temporal(node):
    return (
        node.get("type") == "call" and BY_NAME[node["function"]].category == "temporal"
    )


def integer(node):
    value = node.get("value") if node.get("type") == "literal" else None
    if (
        node.get("type") == "binary"
        and node["operator"] == "subtract"
        and node["left"] == {"type": "literal", "value": 0}
    ):
        value = -integer(node["right"])
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or value != int(value)
        or abs(value) > 10000
    ):
        raise MetadataError(
            "temporal_argument",
            "Period offsets must be whole-number constants between -10,000 and 10,000.",
        )
    return int(value)


def window_spec(node):
    """Return SQL reducer and inclusive relative bounds; None means unbounded past."""
    name, args = node["function"], node["arguments"]
    spec = BY_NAME[name]
    if not spec.minimum <= len(args) <= spec.maximum:
        raise MetadataError(
            "function_arity", f"Invalid argument count for {name.upper()}."
        )
    if name in {"lag", "lead"}:
        offset = integer(args[1])
        if offset < 1:
            raise MetadataError(
                "temporal_argument", "LAG/LEAD require a positive whole-number offset."
            )
        offset *= -1 if name == "lag" else 1
        return "anyOrNull", offset, offset
    operation = args[1].get("value") if args[1].get("type") == "literal" else None
    if not isinstance(operation, str) or operation.upper() not in OPERATIONS:
        raise MetadataError(
            "temporal_operation",
            "Choose SUM, AVG, MIN, MAX, COUNT, STDDEV_POP or STDDEV_SAMP as a quoted operation.",
        )
    start, end = (
        (None, 0) if name == "cumulative" else (integer(args[2]), integer(args[3]))
    )
    if start is not None and start > end:
        raise MetadataError(
            "temporal_argument",
            "The rolling start offset must not exceed its end offset.",
        )
    return OPERATIONS[operation.upper()], start, end
