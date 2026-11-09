from ...warehouse.sql import quote_identifier as q
from ...warehouse.sql import quote_literal as lit

GENERATION = "__semantic_generation"


def token(artifact):
    return artifact.get("generationToken", artifact["artifactId"])


def owner(artifact):
    return artifact.get("storageOwner", "semanticArtifact:" + artifact["artifactId"])


def column_sql(column):
    expression = q(column.get("storageName", column["name"]))
    return f"assumeNotNull({expression})" if not column["nullable"] else expression


def relation_sql(warehouse, artifact):
    if artifact.get("query") or (
        artifact.get("storageMode") in {"current", "source"} and artifact.get("viewName")
    ):
        artifact = {
            **artifact,
            "columns": [{**c, "storageName": c["name"]} for c in artifact["columns"]],
        }
    columns = ", ".join(
        column_sql(c) + " AS " + q(c["name"]) for c in artifact["columns"]
    )
    if artifact.get("query"):
        return f"SELECT {columns} FROM ({artifact['query']})"
    if artifact.get("storageMode") in {"current", "source"}:
        return f"SELECT {columns} FROM {warehouse.qualify(artifact.get('viewName', artifact['physicalName']))}"
    return f"SELECT {columns} FROM {warehouse.qualify(artifact['physicalName'])} WHERE {q(GENERATION)}={lit(token(artifact))}"
