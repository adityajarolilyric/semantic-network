"""Compile a Transaction-owned population as a query, never a physical Dimension."""

from ...warehouse.sql import quote_identifier as q
from ..sql.compilation_context import CompilationContext
from ..transformations.multigrain.contracts import ResolvedTransaction
from ..sql.contracts import Check, digest
from ..transformations.pipeline import InputPipelineCompiler, PreviewTarget


def transaction_population(context, objects, managed, database, max_rows):
    transaction = next(
        o
        for o in objects
        if o["objectId"] == context["owner"]["objectId"] and o["kind"] == "transaction"
    )
    compiler = CompilationContext(
        database, objects, managed, resolution_max_rows=max_rows
    )
    saved = managed.get(transaction["objectId"])
    if saved and saved.get("resourceKind") == "objectView":
        resolved = ResolvedTransaction(
            compiler.cte("SELECT * FROM " + compiler.table(saved["physicalName"])),
            saved["columns"],
            saved["mappings"],
            "",
            [],
        )
    else:
        resolved = InputPipelineCompiler(compiler).compile(
            transaction, PreviewTarget.UNMAPPED_TRANSACTION
        )
    keys = context["definition"]["grain"]["keyIds"]
    source_columns = {c["columnId"]: c for c in resolved.columns}
    sql = compiler.with_sql(
        "SELECT DISTINCT "
        + ", ".join(q(key) for key in keys)
        + " FROM "
        + q(resolved.node)
    )
    nulls = " OR ".join("isNull(" + q(key) + ")" for key in keys)
    checks = [
        Check(check.code, compiler.with_sql(check.sql)) for check in compiler.assertions
    ]
    checks.append(
        Check(
            "null_transaction_key",
            "SELECT * FROM (" + sql + ") WHERE " + nulls + " LIMIT 20",
        )
    )
    return {
        "artifactId": "virtual_" + digest(sql),
        "kind": "query",
        "state": "ready",
        "resourceKind": "transactionGrain",
        "resourceId": context["objectId"],
        "physicalName": "__transaction_population",
        "query": sql,
        "rowCount": None,
        "columns": [
            {
                "columnId": key,
                "name": source_columns[key]["name"],
                "physicalType": source_columns[key]["physicalType"],
            }
            for key in keys
        ],
        "checks": [{"code": check.code, "sql": check.sql} for check in checks],
    }
