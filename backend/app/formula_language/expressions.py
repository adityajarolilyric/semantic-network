"""Structural helpers used by validation, dependency planning and compilation."""

from ..engine.sql.contracts import digest
from .catalog import BY_NAME


def walk(node):
    if not node:
        return
    yield node
    if node["type"] == "binary":
        yield from walk(node["left"])
        yield from walk(node["right"])
    elif node["type"] in {"call", "count"}:
        for argument in node["arguments"]:
            yield from walk(argument)


def contains_counts(node):
    return any(item["type"] == "count" for item in walk(node))


def contains_temporal(node):
    return any(
        item["type"] == "call" and BY_NAME[item["function"]].category == "temporal"
        for item in walk(node)
    )


def temporal_key(node):
    return "@temporal:" + digest(node)[:24]


def definition_references(definition):
    result = list(
        measure_references((definition.get("formula") or {}).get("expression"))
    )
    conversion = definition.get("conversion")
    if conversion:
        result.append(conversion["factorMeasureId"])
    return tuple(dict.fromkeys(result))


def count_key(node):
    return "@count:" + digest(node)[:24]


def measure_references(node):
    """Stable first-use order, including references inside count arguments."""
    return tuple(
        dict.fromkeys(
            item["measureId"] for item in walk(node) if item["type"] == "measureRef"
        )
    )


def conversion_factor_references(node, objects):
    """Factors used as values must be applied before aggregating their context away.

    Counting factor records is not a unit conversion and retains count semantics.
    """
    if not node or node["type"] == "count":
        return ()
    if node["type"] == "measureRef":
        measure_id = node["measureId"]
        return (
            (measure_id,)
            if objects.get(measure_id, {})
            .get("definition", {})
            .get("isConversionFactor")
            else ()
        )
    children = (
        [node["left"], node["right"]]
        if node["type"] == "binary"
        else node.get("arguments", [])
    )
    return tuple(
        dict.fromkeys(
            mid
            for child in children
            for mid in conversion_factor_references(child, objects)
        )
    )
