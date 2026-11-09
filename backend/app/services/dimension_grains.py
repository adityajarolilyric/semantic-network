"""Adapt source-backed Dimensions to the existing grain engine without duplicate authoring."""

from copy import deepcopy

from ..engine.sql.contracts import digest
from ..schemas.dimensions import DimensionInput


def dimension_grain_id(dimension_id):
    return "dg_" + digest(["dimension-grain", dimension_id])[:28]


def dimension_grain(dimension):
    definition = dimension["definition"]
    columns = {column["columnId"]: column for column in definition["columns"]}
    keys = definition["keyColumnIds"]
    transform = {"objectId": dimension["objectId"]}
    if definition.get("pivot"):
        transform["pivot"] = definition["pivot"]
    filters = deepcopy(definition.get("filters"))

    def remap(node):
        if node["type"] == "group":
            for child in node["children"]:
                remap(child)
        else:
            node["columnId"] = columns[node["columnId"]]["sourceColumnId"]

    if filters:
        remap(filters["root"])
        transform["filters"] = filters
    population = {"operation": "source", "sourceId": definition["sourceId"]}
    if len(transform) > 1:
        population["inputTransform"] = transform
    return DimensionInput.model_validate(
        {
            "objectId": dimension_grain_id(dimension["objectId"]),
            "memberCatalogId": dimension["objectId"],
            "memberCatalog": dimension["definition"],
            "isTimeDim": dimension["isTimeDim"],
            "name": dimension["name"],
            "displayName": dimension["name"],
            "description": dimension.get("description", ""),
            "definition": {
                "population": population,
                "grain": {
                    "keyIds": keys,
                    "columns": [
                        {
                            "grainColumnId": key,
                            "name": columns[key]["name"],
                            "populationRef": {
                                "columnId": columns[key]["sourceColumnId"]
                            },
                        }
                        for key in keys
                    ],
                },
                "memberBindings": [
                    {
                        "memberCatalogId": dimension["objectId"],
                        "keyMappings": [
                            {"grainColumnId": key, "memberColumnId": key}
                            for key in keys
                        ],
                        "selectedAttributeColumnIds": [
                            cid
                            for cid in definition["attributeColumnIds"]
                            if cid not in keys
                        ],
                    }
                ],
            },
        }
    ).model_dump()
