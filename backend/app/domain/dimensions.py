"""Dimensions own member catalogs; lookup nodes are transient engine projections."""

from copy import deepcopy

from ..core.errors import MetadataError
from ..engine.contracts import digest


def expand_member_catalogs(objects):
    result = {obj["objectId"]: obj for obj in objects}
    owners = {}
    for dimension in objects:
        catalog = dimension.get("memberCatalog")
        if dimension["kind"] != "dimension" or not catalog:
            continue

        catalog_id = dimension["memberCatalogId"]
        if catalog_id in owners and owners[catalog_id] != dimension["objectId"]:
            raise MetadataError(
                "duplicate_member_catalog",
                "Two Dimensions cannot own the same member catalog.",
            )
        if catalog_id in result and result[catalog_id]["kind"] != "dimensionMembers":
            raise MetadataError(
                "duplicate_member_catalog",
                "A member catalog identity conflicts with another semantic object.",
            )
        owners[catalog_id] = dimension["objectId"]
        document = {
            "objectId": catalog_id,
            "kind": "dimensionMembers",
            "name": dimension["name"],
            "displayName": dimension["name"],
            "description": dimension.get("description", ""),
            "isTimeDim": dimension.get("isTimeDim", False),
            "definition": deepcopy(catalog),
        }
        document.update(
            {
                key: dimension[key]
                for key in (
                    "modelId",
                    "revisionId",
                    "version",
                    "createdAt",
                    "updatedAt",
                )
                if key in dimension
            }
        )
        result[document["objectId"]] = document
    return list(result.values())


def dimension_columns(dimension, objects):
    """Public output columns, including selected relationship enrichment."""

    by_id = {obj["objectId"]: obj for obj in objects}
    definition = dimension["definition"]
    own_members = (dimension.get("memberCatalog") or {}).get("columns", [])
    member_names = {c["columnId"]: c["displayName"] for c in own_members}
    columns = [
        {
            "columnId": c["grainColumnId"],
            "name": member_names.get(c["grainColumnId"], c["name"]),
            **(
                {
                    "memberCatalogId": dimension["memberCatalogId"],
                    "memberColumnId": c["grainColumnId"],
                }
                if c["grainColumnId"] in member_names
                else {}
            ),
        }
        for c in definition["grain"]["columns"]
    ]
    columns += [
        {"columnId": c["columnId"], "name": c["name"]}
        for c in definition.get("populationAttributes", [])
    ]
    for binding in definition["memberBindings"]:
        catalog = by_id.get(binding["memberCatalogId"])
        if not catalog:
            continue
        for column in catalog["definition"]["columns"]:
            if column["columnId"] in binding["selectedAttributeColumnIds"]:
                columns.append(
                    {
                        "columnId": "da_"
                        + digest(
                            [
                                dimension["objectId"],
                                catalog["objectId"],
                                column["columnId"],
                            ]
                        )[:28],
                        "name": column["displayName"],
                        "memberCatalogId": catalog["objectId"],
                        "memberColumnId": column["columnId"],
                    }
                )
    return columns
