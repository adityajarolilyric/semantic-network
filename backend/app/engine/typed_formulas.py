"""Stable attribute aliases and resolved coordinates for formula evaluation."""

from ..core.errors import MetadataError
from ..domain.grains import transaction_column_reference
from .contracts import digest
from .relationships import Coordinate


def attribute_alias(node):
    return "__formula_attr_" + digest(node)[:20]


def attribute_coordinate(host, node, target):
    if node.get("memberCatalogId"):
        coordinate = Coordinate(
            frozenset({("dimensionMembers", node["memberCatalogId"], node["columnId"])})
        )
    elif node.get("transactionId"):
        grain_id, column_id = transaction_column_reference(
            node["transactionId"], node["columnId"], list(host.objects.values())
        )
        coordinate = host.relationships.outputs(grain_id).get(column_id)
    else:
        coordinate = host.relationships.outputs(node["dimensionId"]).get(
            node["columnId"]
        )
    resolved = host.relationships.resolve(target, coordinate) if coordinate else None
    if not resolved:
        raise MetadataError(
            "formula_grain_unavailable",
            "A formula attribute is not determined at the requested grain.",
        )
    return resolved
