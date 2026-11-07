"""Persist bounded execution traces separately from logical compiler plans."""

from ..core.errors import MetadataError
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import RunInputLookup
from ..warehouse.sql_trace import trace_script


class SqlHistory:
    def __init__(self, store):
        self.store = store

    async def save(self, run, trace):
        inputs = metadata_repositories(self.store.repository).inputs
        prefix = f"__sql_{run['fence']}_"
        ids = []
        for index, statement in enumerate(trace.statements):
            identity = prefix + str(index).zfill(4)
            ids.append(identity)
            await inputs.insert(
                {"runId": run["runId"], "inputId": identity, "document": statement}
            )
        await inputs.insert(
            {
                "runId": run["runId"],
                "inputId": prefix + "manifest",
                "document": {"ids": ids, "omitted": trace.omitted},
            }
        )

    async def read(self, run_id, attempt):
        inputs = metadata_repositories(self.store.repository).inputs
        prefix = f"__sql_{attempt}_"
        manifest = await inputs.find(
            RunInputLookup(run_id=run_id, input_id=prefix + "manifest")
        )
        if not manifest:
            raise MetadataError(
                "build_sql_not_captured",
                "Executed Build SQL is unavailable for this older build. It will be captured on the next rebuild.",
                409,
            )
        statements = []
        for identity in manifest["document"]["ids"]:
            row = await inputs.require(
                RunInputLookup(run_id=run_id, input_id=identity), "SQL statement"
            )
            statements.append(row["document"])
        omitted = manifest["document"]["omitted"]
        return {
            "sql": trace_script(statements),
            "dialect": "clickhouse",
            "statementCount": len(statements),
            "warning": f"{omitted} statements omitted because the diagnostic capture limit was reached."
            if omitted
            else None,
            "runId": run_id,
        }
