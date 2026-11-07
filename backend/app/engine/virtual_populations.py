"""Single-source Dimensions retain their semantic population without a copy table."""

from ..warehouse.sql import quote_identifier as q
from .contracts import digest


def is_virtual_dimension(dimension):
    return dimension["definition"]["population"]["operation"] == "source"


def logical_population_query(compiled):
    columns = ", ".join(
        q(c["name"]) + " AS " + q(c["columnId"]) for c in compiled.schema.columns
    )
    return f"SELECT {columns} FROM ({compiled.sql})"


def virtual_population(dimension, compiled, input_hash):
    columns = list(compiled.schema.columns)
    keys = dimension["definition"]["grain"]["keyIds"]
    key_columns = [q(c["name"]) for c in columns if c["columnId"] in keys]
    nulls = " OR ".join("isNull(" + key + ")" for key in key_columns)
    checks = [{"code": c.code, "sql": c.sql} for c in compiled.checks]
    checks.extend(
        [
            {
                "code": "null_planning_key",
                "sql": f"SELECT * FROM ({compiled.sql}) WHERE {nulls} LIMIT 20",
            },
            {
                "code": "duplicate_planning_key",
                "sql": f"SELECT {', '.join(key_columns)}, count() FROM ({compiled.sql}) GROUP BY {', '.join(key_columns)} HAVING count()>1 LIMIT 20",
            },
        ]
    )
    return {
        "artifactId": "virtual_"
        + digest([dimension["definition"], input_hash, compiled.compiler_version]),
        "kind": "query",
        "state": "ready",
        "resourceKind": "dimension",
        "resourceId": dimension["objectId"],
        "physicalName": "__virtual_dimension",
        "columns": columns,
        "query": compiled.sql,
        "logicalQuery": logical_population_query(compiled),
        "rowCount": None,
        "checks": checks,
    }
