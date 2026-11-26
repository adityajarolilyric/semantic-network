"""Discover typed, grain-safe formula inputs from an effective metadata snapshot."""

from ..core.errors import MetadataError
from ..engine.graph.relationships import Coordinate, LevelRelationshipAnalyzer
from ..validation.graph import SemanticGraphValidator
from .grains import transaction_column_reference
from .typed_values import result_type


def formula_attributes(objects, level_id):
    context = SemanticGraphValidator(objects)
    analyzer = LevelRelationshipAnalyzer(objects)
    level = context.objects.get(level_id)
    if not level or level["kind"] not in {"dimension", "grain"}:
        raise MetadataError(
            "invalid_formula_level", "Choose an existing Output Dimension."
        )
    result = []
    for obj in objects:
        if obj["kind"] == "dimensionMembers":
            allowed = set(
                obj["definition"]["keyColumnIds"]
                + obj["definition"]["attributeColumnIds"]
            )
            for column in obj["definition"]["columns"]:
                cid = column["columnId"]
                if cid not in allowed:
                    continue
                coordinate = Coordinate(
                    frozenset({("dimensionMembers", obj["objectId"], cid)})
                )
                if not analyzer.resolve(level_id, coordinate):
                    continue
                dtype = result_type(context.dimension_type(obj, cid) or "")
                if dtype:
                    result.append(
                        {
                            "label": obj["name"] + "." + column["displayName"],
                            "key": obj["objectId"] + "." + cid,
                            "aliases": [obj["name"] + "." + column["name"]],
                            "reference": {
                                "type": "attributeRef",
                                "memberCatalogId": obj["objectId"],
                                "columnId": cid,
                            },
                            "valueType": dtype["type"],
                        }
                    )
    for owner in (
        obj
        for obj in objects
        if obj["kind"] == "dimension" and not obj.get("memberCatalogId")
    ):
        names = {
            c.get("grainColumnId", c.get("columnId")): c["name"]
            for c in [
                *owner["definition"]["grain"]["columns"],
                *owner["definition"].get("populationAttributes", []),
            ]
        }
        for cid, coordinate in analyzer.outputs(owner["objectId"]).items():
            if cid not in names or not analyzer.resolve(level_id, coordinate):
                continue
            dtype = result_type(context.grain_type(owner, cid) or "")
            if dtype:
                result.append(
                    {
                        "label": owner["name"] + "." + names[cid],
                        "key": owner["objectId"] + "." + cid,
                        "reference": {
                            "type": "attributeRef",
                            "dimensionId": owner["objectId"],
                            "columnId": cid,
                        },
                        "valueType": dtype["type"],
                    }
                )
    for transaction in (obj for obj in objects if obj["kind"] == "transaction"):
        for mapping in transaction["definition"].get("keyMappings", []):
            source_column = mapping["sourceColumnId"]
            grain_id, column_id = transaction_column_reference(
                transaction["objectId"], source_column, objects
            )
            coordinate = analyzer.outputs(grain_id).get(column_id)
            if not coordinate or not analyzer.resolve(level_id, coordinate):
                continue
            grain = context.objects.get(grain_id)
            dtype = result_type(context.grain_type(grain, column_id) or "")
            if not dtype:
                continue
            name = next(
                (
                    c["name"]
                    for c in grain["definition"]["grain"]["columns"]
                    if c["grainColumnId"] == column_id
                ),
                source_column,
            )
            result.append(
                {
                    "label": transaction["name"] + "." + name,
                    "key": transaction["objectId"] + "." + source_column,
                    "reference": {
                        "type": "attributeRef",
                        "transactionId": transaction["objectId"],
                        "columnId": source_column,
                    },
                    "valueType": dtype["type"],
                }
            )
    return result
