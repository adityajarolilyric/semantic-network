from time import perf_counter
from uuid import uuid4

from ....core.errors import MetadataError
from ....core.metadata import revision_scope as scope
from ....core.metadata import utc_now as now
from ....domain.measure_policies import MEASURE_TYPE_POLICY
from ....repositories.metadata import metadata_repositories
from ....warehouse.sql import quote_identifier as q
from ...sql.contracts import DimensionPlan, digest
from ..storage.column_updates import update_columns
from ..storage.layout import time_key_ids
from .source_values import SourceValueBuilder


class MeasurePopulationBuilder:
    def __init__(self, builds):
        self.builds = builds
        self.source_values = SourceValueBuilder(builds)

    async def build(self, run, worker, prepared, dimension_id):
        builds = self.builds
        source_binding = self.source_values.binding(prepared, dimension_id)
        if source_binding:
            return await self.source_values.build(run, worker, prepared, dimension_id, source_binding)
        objects = prepared.objects
        populations = prepared.populations
        snapshots = prepared.snapshots
        manifests = prepared.manifests
        compiler = prepared.compiler
        objects_by_id = {document["objectId"]: document for document in objects}
        level_started = perf_counter()
        compiled, unavailable = compiler.compile_level(dimension_id)
        if (
            run.get("isEdit")
            and set(snapshots[dimension_id].get("measureColumns", {}))
            & unavailable.keys()
        ):
            raise MetadataError(
                "edit_dependency_unavailable",
                "An existing measure cannot be recomputed at this level. No edit was activated.",
            )
        if len(compiled.sql.encode()) > builds.settings.measure_max_sql_bytes:
            raise MetadataError(
                "measure_sql_limit",
                "Compiled measure graph exceeds the configured SQL-size limit.",
            )
        compiled_at = perf_counter()
        step = "values_" + dimension_id
        await worker.step(
            run,
            step,
            "running",
            planHash=digest(compiled.sql),
            compilerVersion=compiled.compiler_version,
            queryMemoryBytes=builds.executor.execution_settings()["max_memory_usage"],
            unavailable=unavailable,
        )
        prechecks_started = perf_counter()
        await builds.executor.checks(compiled)
        prechecks_ms = (perf_counter() - prechecks_started) * 1000
        aid = "artifact_" + uuid4().hex
        artifact = {
            **scope(run["modelId"], run["revisionId"]),
            "artifactId": aid,
            "runId": run["runId"],
            "resourceKind": "measureValues",
            "resourceId": dimension_id,
            "kind": "table",
            "state": "candidate",
            "createdAt": now(),
            "inputHash": run["inputHash"],
            "attempt": run["fence"],
        }
        previous_target = snapshots.get(dimension_id)
        changed_columns = (
            update_columns(
                previous_target,
                populations[dimension_id],
                manifests[dimension_id],
                compiled,
                run.get("forcedMeasures", []),
            )
            if run.get("networkRunId")
            and (not run.get("isEdit"))
            and (not run.get("recoveryBuild"))
            else None
        )

        edit_scopes = {}
        if run.get("isEdit"):
            edit_scopes = {
                mid: node
                for (mid, level), node in compiler.cell_impact.scopes.items()
                if level == dimension_id
                and node
                and mid in snapshots[dimension_id].get("measureColumns", {})
            }
            changed_columns = [
                column["columnId"]
                for column in compiled.schema.columns
                if (
                    column.get("measureId")
                    or column.get("coverageFor")
                    or column.get("mixedFor")
                )
                in edit_scopes
            ]

        async def record(repo):
            await worker.fenced(repo, run)
            artifact.update(
                await builds.storage.reserve(
                    repo, artifact, objects_by_id[dimension_id]["name"]
                )
            )
            if run.get("isEdit"):
                artifact.update(
                    writeMode="in_place",
                    updateColumnIds=changed_columns,
                    rowScopeNodes=edit_scopes,
                    measureOrder=list(
                        dict.fromkeys(
                            dep
                            for mid in compiler.measures
                            for dep in compiler.dependencies.ordered(mid)
                        )
                    ),
                    measureDependencies={
                        mid: sorted(deps)
                        for mid, deps in compiler.dependencies.direct.items()
                    },
                    measureNodes={
                        mid: compiler.cache.get((mid, dimension_id))
                        for mid in compiler.measures
                    },
                )
            if run.get("networkRunId") and (not run.get("isEdit")):
                measure_order = list(
                    dict.fromkeys(
                        (
                            dependency
                            for mid in compiler.measures
                            for dependency in compiler.dependencies.ordered(mid)
                        )
                    )
                )
                artifact.update(
                    recoveryBuild=run.get("recoveryBuild", False),
                    writeMode="in_place"
                    if changed_columns is not None
                    else "column_build",
                    updateColumnIds=changed_columns
                    if changed_columns is not None
                    else [
                        c["columnId"]
                        for c in compiled.schema.columns
                        if c.get("measureId")
                        or c.get("coverageFor")
                        or c.get("mixedFor")
                    ],
                    measureOrder=measure_order,
                    measureDependencies={
                        mid: sorted(deps)
                        for mid, deps in compiler.dependencies.direct.items()
                    },
                    measureNodes={
                        mid: compiler.cache.get((mid, dimension_id))
                        for mid in compiler.measures
                    },
                )
                if changed_columns is None:
                    population_columns = populations[dimension_id]["columns"]
                    artifact["populationQuery"] = (
                        "SELECT "
                        + ", ".join(
                            (
                                q(c["columnId"]) + " AS " + q(c["name"])
                                for c in population_columns
                            )
                        )
                        + " FROM ("
                        + compiler.population(dimension_id)
                        + ")"
                    )
                await builds.gate.mark_writing(repo, run)
            await metadata_repositories(repo).artifacts.insert(artifact)

        await builds.store.transaction(record)
        keys = tuple(compiler.keys(dimension_id))
        plan = DimensionPlan(None, compiled.schema, keys, (), (), (), False)
        artifact["timeKeyIds"] = list(
            time_key_ids(objects_by_id[dimension_id], objects)
        )
        artifact["sourceSnapshot"] = (
            {
                k: previous_target[k]
                for k in ("storageMode", "physicalName", "sortKeyIds", "columns")
                if k in previous_target
            }
            if previous_target
            else None
        )
        outcome = await builds.executor.build(artifact, compiled, plan)
        if (
            populations[dimension_id].get("rowCount") is not None
            and outcome["rowCount"] != populations[dimension_id]["rowCount"]
        ):
            raise MetadataError(
                "measure_cardinality",
                "Measure merging changed the planning population row count.",
            )
        postchecks_started = perf_counter()
        await builds.executor.checks(compiled)
        postchecks_ms = (perf_counter() - postchecks_started) * 1000
        populated = {
            **artifact,
            **outcome,
            "state": "ready",
            "builtAt": now(),
            "levelId": dimension_id,
            "generationId": run["runId"],
            "numericPolicy": MEASURE_TYPE_POLICY,
            "materialisationManifest": manifests.get(dimension_id),
            "keyColumns": {
                c["columnId"]: c.get("storageName", c["name"])
                for c in outcome["columns"]
                if c["columnId"] in keys
            },
            "measureColumns": {
                c["measureId"]: c.get("storageName", c["name"])
                for c in outcome["columns"]
                if c.get("measureId")
            },
            "coverageColumns": {
                c["coverageFor"]: c.get("storageName", c["name"])
                for c in outcome["columns"]
                if c.get("coverageFor")
            },
            "mixedColumns": {
                c["mixedFor"]: c.get("storageName", c["name"])
                for c in outcome["columns"]
                if c.get("mixedFor")
            },
            "unavailable": unavailable,
            "populationArtifactId": populations[dimension_id]["artifactId"],
            "compilerVersion": compiled.compiler_version,
            "derivations": [
                {
                    "measureId": n.measure_id,
                    "dimensionId": n.level_id,
                    "operation": n.operation,
                }
                for n in compiler.nodes
                if n.level_id == dimension_id
            ],
        }
        if run.get("isEdit"):
            # Later grains read this completed private candidate instead of expanding
            # its already-applied patch/formula DAG again. Frozen edit weights still
            # use the original captured snapshots.
            for mid in populated["measureColumns"]:
                node = compiler.cache.get((mid, dimension_id))
                if node is None:
                    continue
                read = compiler.stored_measure(
                    mid, dimension_id, {dimension_id: populated}
                )
                compiler.ctes[node] = compiler.ctes[read]
                compiler.cte_dependencies[node] = set()
                if read in compiler.shared_relations:
                    compiler.shared_relations[node] = compiler.shared_relations[read]
        if run.get("networkRunId") and (not run.get("isEdit")):
            for mid in populated["measureColumns"]:
                stored = compiler.stored_measure(
                    mid, dimension_id, {dimension_id: populated}
                )
                compiler.cache[mid, dimension_id] = stored
                if (
                    compiler.measures[mid]["definition"]["nativeGrainId"]
                    == dimension_id
                ):
                    compiler.cache[mid, "__native_seed"] = stored
            final_read, _ = compiler.compile_level(dimension_id, include_checks=False)
        else:
            final_read = compiled
        await worker.step(
            run,
            step,
            "succeeded",
            rowCount=outcome["rowCount"],
            sql=final_read.formatted_sql,
            queryMemoryBytes=builds.executor.execution_settings()["max_memory_usage"],
            calculationSql=compiled.formatted_sql,
            artifactId=aid,
            reusedMeasures=sorted(
                {
                    n.measure_id
                    for n in compiler.nodes
                    if n.level_id == dimension_id and n.operation == "reuse"
                }
            ),
            writeMode=outcome.get("writeMode", "new_generation"),
            updatedColumns=outcome.get("updatedColumns", []),
            timingsMs={
                "compile": round((compiled_at - level_started) * 1000, 3),
                "preChecks": round(prechecks_ms, 3),
                "tableBuild": outcome.get("buildTimingsMs", {}),
                "postChecks": round(postchecks_ms, 3),
                "total": round((perf_counter() - level_started) * 1000, 3),
            },
        )
        return populated
