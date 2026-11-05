"""Discover typed, grain-safe formula inputs from an effective metadata snapshot."""

from ..engine.relationships import Coordinate, LevelRelationshipAnalyzer
from ..validation.graph import SemanticGraphValidator
from .typed_values import result_type


def formula_attributes(objects, level_id):
    context = SemanticGraphValidator(objects)
    analyzer = LevelRelationshipAnalyzer(objects)
    level = context.objects.get(level_id)
    if not level or level["kind"] != "planningLevel":
        from ..core.errors import MetadataError

        raise MetadataError(
            "invalid_formula_level", "Choose an existing Output Planning Level."
        )
    result = []
    for obj in objects:
        if obj["kind"] == "dimension":
            allowed = set(
                obj["definition"]["keyColumnIds"]
                + obj["definition"]["attributeColumnIds"]
            )
            for column in obj["definition"]["columns"]:
                cid = column["columnId"]
                if cid not in allowed:
                    continue
                coordinate = Coordinate(
                    frozenset({("dimension", obj["objectId"], cid)})
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
                                "dimensionId": obj["objectId"],
                                "columnId": cid,
                            },
                            "valueType": dtype["type"],
                        }
                    )
    for owner in (obj for obj in objects if obj["kind"] == "planningLevel"):
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
                            "planningLevelId": owner["objectId"],
                            "columnId": cid,
                        },
                        "valueType": dtype["type"],
                    }
                )
    return result
