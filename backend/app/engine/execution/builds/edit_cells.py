"""Freeze an edit's small changed-cell patch in ClickHouse for this fenced attempt."""

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from ....core.metadata import revision_scope, utc_now
from ....repositories.metadata import metadata_repositories
from ....warehouse.sql import quote_identifier as q
from ...sql.contracts import Check, ExecutionPlan
from ...measures.contracts import VALUE


class EditCellStage:
    def __init__(self, builds):
        self.builds = builds

    @asynccontextmanager
    async def hold(self, run, worker, compiler):
        if not run.get("isEdit"):
            yield
            return
        builds = self.builds
        with compiler.cell_impact.previous_values():
            compiler.patch()
        # Validate allocation before freezing weights/new values. Only scalars/diagnostics return to Python.
        checks = tuple(
            Check(check.code, compiler.with_sql(check.sql))
            for check in compiler.assertions
        )
        await builds.executor.checks(ExecutionPlan(None, checks, None))
        aid = "artifact_" + uuid4().hex
        physical = "__semantic_edit_build_" + uuid4().hex
        artifact = {
            **revision_scope(run["modelId"], run["revisionId"]),
            "artifactId": aid,
            "runId": run["runId"],
            "attempt": run["fence"],
            "resourceKind": "editCells",
            "resourceId": compiler.write_id,
            "kind": "table",
            "state": "candidate",
            "createdAt": utc_now(),
            "storageMode": "current",
            "physicalName": physical,
            "viewName": physical + "_view",
            "liveName": physical + "_unpublished",
            "buildOwner": "semanticEditCells:" + aid,
            "cleanupPending": True,
        }

        async def reserve(repo):
            await worker.fenced(repo, run)
            await metadata_repositories(repo).artifacts.insert(artifact)

        await builds.store.transaction(reserve)
        try:
            await builds.storage.require_running(artifact)
            delta = compiler.edit_patch.state["delta"]
            sql = compiler.with_sql(f"SELECT * FROM {q(delta)}")
            previous_columns = compiler.snapshots[compiler.write_id]["columns"]
            columns = [
                {**column, "name": column["columnId"], "nullable": False}
                for column in previous_columns
                if column["columnId"] in compiler.keys(compiler.write_id)
            ]
            order = {
                key: index for index, key in enumerate(compiler.keys(compiler.write_id))
            }
            columns.sort(key=lambda column: order[column["columnId"]])
            value_column = next(
                column
                for column in previous_columns
                if column.get("measureId") == compiler.edit.measureId
            )
            columns.extend(
                [
                    {**value_column, "name": VALUE, "nullable": True},
                    {"name": "__edited", "physicalType": "UInt8", "nullable": False},
                ]
            )
            await builds.executor.warehouse.queries.stage_edit_cells(
                artifact, sql, columns, builds.executor.execution_settings()
            )
            await builds.storage.require_running(artifact)
            keys = ", ".join(q(key) for key in compiler.keys(compiler.write_id))
            compiler.ctes[delta] = (
                f"SELECT {keys}, {q(VALUE)}, toUInt8(1) AS __edited FROM {compiler.table(physical)}"
            )
            compiler.cte_dependencies[delta] = set()
            yield
        finally:
            try:
                await builds.storage.rollback(artifact)
            except Exception:
                # The durable ownership/fence descriptor allows normal recovery after a crash or disconnect.
                logging.getLogger(__name__).exception(
                    "edit_cell_stage_cleanup_deferred"
                )
