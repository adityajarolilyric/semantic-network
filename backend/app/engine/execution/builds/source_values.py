"""Build an independent Transaction output as an owned view over its source."""

from copy import deepcopy
from uuid import uuid4

from ....core.metadata import revision_scope, utc_now
from ....core.errors import MetadataError, Conflict
from ....domain.measure_policies import MEASURE_TYPE_POLICY
from ....repositories.metadata import metadata_repositories
from ....repositories.metadata.criteria import (
    TableResourceLookup,
    BindingLookup,
    ArtifactLookup,
)
from ....warehouse.source_writeback import SourceWriteback
from ...measures.compiler import MeasureCompiler
from ...populations.transaction import transaction_population
from ...sql.contracts import digest


class SourceValueBuilder:
    def __init__(self, builds):
        self.builds = builds
        self.writeback = SourceWriteback(builds.executor.warehouse)

    def binding(self, prepared, grain_id):
        return prepared.compiler.source_storage.binding(grain_id)

    async def build(self, run, worker, prepared, grain_id, binding):
        builds = self.builds
        previous = prepared.snapshots.get(grain_id)
        if (
            run.get("buildMode") == "incremental"
            and previous
            and previous.get("storageMode") != "source"
        ):
            raise Conflict(
                "Use Full Semantic Refresh once to switch this standalone Transaction to source-backed storage. Existing materialised edits are unchanged until then."
            )
        storage = builds.storage.source_values
        aid = "artifact_" + uuid4().hex
        label = "".join(
            char if char.isalnum() or char == "_" else "_"
            for char in binding["tableName"]
        )[:60]
        name = "trans_" + label + "_values_" + digest(aid)[:20]
        artifact = {
            **revision_scope(run["modelId"], run["revisionId"]),
            "artifactId": aid,
            "runId": run["runId"],
            "resourceKind": "measureValues",
            "resourceId": grain_id,
            "kind": "view",
            "storageMode": "source",
            "state": "candidate",
            "createdAt": utc_now(),
            "attempt": run["fence"],
            "physicalName": name,
            "viewName": name,
            "sourceTableName": binding["tableName"],
            "sourceId": binding["sourceId"],
            **(
                {
                    "managedSourceViewName": prepared.compiler.managed[
                        binding["sourceId"]
                    ]["physicalName"]
                }
                if prepared.compiler.managed[binding["sourceId"]].get("storageMode")
                != "source_input"
                else {}
            ),
            "buildOwner": "semanticSourceEdit:" + aid,
            "cleanupPending": True,
            "inputHash": run["inputHash"],
            "numericPolicy": MEASURE_TYPE_POLICY,
        }

        managed_input = prepared.compiler.managed[binding["sourceId"]]
        if managed_input.get("storageMode") != "source_input":
            artifact["retiredInputView"] = {
                "artifactId": managed_input["artifactId"],
                "physicalName": managed_input["physicalName"],
            }

        async def reserve(repo):
            await worker.fenced(repo, run)
            repositories = metadata_repositories(repo)
            active_binding = await repositories.bindings.find(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    resource_kind="measureValues",
                    resource_id=grain_id,
                )
            )
            active_output = (
                await repositories.artifacts.require(
                    ArtifactLookup(artifact_id=active_binding["artifactId"]),
                    "Active source output",
                )
                if active_binding
                else None
            )
            if active_output and active_output.get("storageMode") == "source":
                artifact["previousOutput"] = {
                    "artifactId": active_output["artifactId"],
                    "physicalName": active_output["physicalName"],
                }
            resource = await repositories.table_resources.find(
                TableResourceLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    resource_kind="dimension",
                    resource_id=grain_id,
                )
            )
            if resource:
                artifact["previousMaterialisation"] = {
                    key: resource[key]
                    for key in ("physicalName", "viewName", "storageOwner")
                    if key in resource
                }
            await repositories.artifacts.insert(artifact)

        await builds.store.transaction(reserve)
        managed = deepcopy(prepared.compiler.managed)
        managed[binding["sourceId"]]["physicalName"] = binding["tableName"]
        edit_delta = (
            prepared.compiler.edit_patch.state.get("delta")
            if run.get("isEdit")
            else None
        )
        delta_sql = (
            prepared.compiler.with_sql('SELECT * FROM "' + edit_delta + '"')
            if edit_delta
            else None
        )
        has_changes = delta_sql and await storage.has_rows(
            delta_sql, builds.executor.execution_settings()
        )
        if run.get("isEdit") and prepared.compiler.write_id == grain_id and has_changes:
            edited = prepared.compiler.measures[prepared.compiler.edit.measureId]
            physical = await storage.clone(artifact)
            delta = prepared.compiler.edit_patch.state["delta"]
            delta_sql = prepared.compiler.with_sql('SELECT * FROM "' + delta + '"')
            allocation = edited["definition"].get("allocation") or {}
            basis = prepared.compiler.measures.get(
                allocation.get("basisMeasureId"), {}
            ).get("definition", {})
            basis_column = (
                binding["columns"].get(basis.get("valueColumnId"), {}).get("name")
                if basis.get("transactionId") == binding["transactionId"]
                else None
            )
            conversion = edited["definition"].get("conversion")
            factor_sql = None
            if conversion:
                with prepared.compiler.cell_impact.previous_values():
                    factor = prepared.compiler.stored(
                        conversion["factorMeasureId"], grain_id
                    )
                factor_sql = prepared.compiler.with_sql(
                    'SELECT * FROM "' + factor + '"'
                )
            artifact["sourcePromotions"] = await self.writeback.apply(
                artifact,
                binding,
                edited,
                delta_sql,
                builds.executor.execution_settings(),
                basis_column=basis_column,
                factor_sql=factor_sql,
            )
            managed[binding["sourceId"]]["physicalName"] = physical
            managed.pop(binding["transactionId"], None)
        # A source view must never reference an earlier output view: that would
        # retain stale definitions and produce cycles on incremental rebuilds.
        populations = dict(prepared.populations)
        context = prepared.compiler.objects[grain_id]
        populations[grain_id] = transaction_population(
            context,
            prepared.objects,
            managed,
            builds.settings.clickhouse_database,
            builds.settings.resolution_max_rows,
        )
        compiler = MeasureCompiler(
            builds.settings.clickhouse_database,
            prepared.objects,
            populations,
            managed,
            prepared.compiler.base_id,
        )
        compiler.max_nodes = builds.settings.measure_max_nodes
        compiled, unavailable = compiler.compile_level(grain_id)
        if len(compiled.sql.encode()) > builds.settings.measure_max_sql_bytes:
            raise MetadataError(
                "measure_sql_limit",
                "Compiled source output exceeds the configured SQL-size limit.",
            )
        await builds.executor.checks(compiled)
        artifact["sourceQuery"] = compiled.sql
        if (
            artifact.get("sourceCloneName")
            and binding["transactionId"] in prepared.compiler.managed
        ):
            live_compiler = MeasureCompiler(
                builds.settings.clickhouse_database,
                prepared.objects,
                prepared.populations,
                prepared.compiler.managed,
                prepared.compiler.base_id,
            )
            artifact["sourceLiveQuery"] = live_compiler.compile_level(grain_id)[0].sql
        outcome = await storage.create_view(
            artifact, compiled, builds.executor.execution_settings()
        )
        if outcome["rowCount"] > builds.settings.planning_max_rows:
            raise MetadataError(
                "planning_row_limit", "Source output exceeds the configured row limit."
            )
        keys = compiler.keys(grain_id)
        artifact.update(
            **outcome,
            state="ready",
            builtAt=utc_now(),
            levelId=grain_id,
            generationId=run["runId"],
            materialisationManifest=prepared.manifests[grain_id],
            populationArtifactId=prepared.populations[grain_id]["artifactId"],
            compilerVersion=compiled.compiler_version,
            unavailable=unavailable,
            keyColumns={
                column["columnId"]: column["name"]
                for column in outcome["columns"]
                if column["columnId"] in keys
            },
            measureColumns={
                column["measureId"]: column["name"]
                for column in outcome["columns"]
                if column.get("measureId")
            },
            coverageColumns={
                column["coverageFor"]: column["name"]
                for column in outcome["columns"]
                if column.get("coverageFor")
            },
            mixedColumns={
                column["mixedFor"]: column["name"]
                for column in outcome["columns"]
                if column.get("mixedFor")
            },
        )
        await storage.save(artifact)
        await worker.step(
            run,
            "values_" + grain_id,
            "succeeded",
            artifactId=aid,
            rowCount=outcome["rowCount"],
            sql=compiled.formatted_sql,
            writeMode="source_view",
        )
        return artifact
