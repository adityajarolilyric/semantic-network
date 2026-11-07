"""Readable managed-source names, without changing source table identities."""

import re

from ..domain.typed_values import NUMERIC, result_type

VIEW_NAMING_POLICY = "readable-source-views-v1"


def source_view_role(source, objects):
    source_id = source["objectId"]
    if any(
        o["kind"] == "transaction" and o["definition"]["sourceId"] == source_id
        for o in objects
    ):
        return "trans"
    for obj in objects:
        if (
            obj["kind"] == "dimensionMembers"
            and obj["definition"]["sourceId"] == source_id
        ):
            return "dim"
        if obj["kind"] == "dimension":
            definition = obj["definition"]
            population = definition["population"]
            if (
                (obj.get("memberCatalog") or {}).get("sourceId") == source_id
                or population.get("sourceId") == source_id
                or any(
                    f["sourceId"] == source_id for f in population.get("factors", [])
                )
            ):
                return "dim"
    return (
        "trans"
        if any(
            (result_type(c["physicalType"]) or {}).get("type") in NUMERIC
            for c in source["definition"]["columns"]
        )
        else "dim"
    )


def source_view_name(source, artifact_id, role):
    label = (
        re.sub(r"[^A-Za-z0-9_]+", "_", source["definition"]["tableName"]).strip("_")[
            :80
        ]
        or "source"
    )
    return f"{role}_{label}_{artifact_id.removeprefix('artifact_')}"


def has_readable_name(artifact, source, objects):
    role = source_view_role(source, objects)
    return artifact.get("viewNamingPolicy") == VIEW_NAMING_POLICY and artifact.get(
        "physicalName"
    ) == source_view_name(source, artifact["artifactId"], role)
