"""Stable attribute aliases and resolved coordinates for formula evaluation."""

from ..core.errors import MetadataError
from .contracts import digest
from .relationships import Coordinate


def attribute_alias(node):
    return "__formula_attr_" + digest(node)[:20]


def attribute_coordinate(host, node, target):
    if node.get("dimensionId"):
        coordinate = Coordinate(
            frozenset({("dimension", node["dimensionId"], node["columnId"])})
        )
    else:
        coordinate = host.relationships.outputs(node["planningLevelId"]).get(
            node["columnId"]
        )
    resolved = host.relationships.resolve(target, coordinate) if coordinate else None
    if not resolved:
        raise MetadataError(
            "formula_grain_unavailable",
            "A formula attribute is not determined at the requested grain.",
        )
    return resolved
