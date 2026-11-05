"""Measure candidate execution and guarded activation; HTTP services capture intent."""

from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    RunInputLookup,
)

from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..core.metadata import revision_scope as scope
from ..core.metadata import utc_now as now
from ..domain.measure_policies import MEASURE_TYPE_POLICY
from ..engine.captured_inputs import CapturedRunInputs
from ..engine.contracts import PlanningPlan, digest
from ..engine.date_range_resolution import calendar_source_ids
from ..engine.incremental import (
    IncrementalMeasureCompiler,
)
from ..engine.measure_compiler import (
    MeasureCompiler,
)
from ..engine.table_storage import STORAGE_POLICY
from ..engine.transaction_resolution import (
    prepare_resolution_profiles,
)


@dataclass(frozen=True)
class MeasureBuildCandidate:
    artifacts: list[dict]
    populations: dict
    snapshots: dict
    previous: dict | None


class MeasureBuildHandler:
    def __init__(self, builds):
        self.builds = builds
        self.activation = MeasureBuildActivation(builds)

    async def execute_run(self, run, worker):
        builds = self.builds
        if run.get("storagePolicy") != STORAGE_POLICY:
            raise Conflict(
                "This build predates the table-storage upgrade. Submit a new build."
            )
        builds.incremental.validate_runtime_policy(run)
        captured_inputs = await CapturedRunInputs.load(
            builds.store.repository, run["runId"], builds.max_objects * 4 + 3
        )
        docs = captured_inputs.documents
        objects = captured_inputs.objects
        populations = captured_inputs.populations
        managed = captured_inputs.managed_artifacts
        managed_definitions = captured_inputs.managed_definitions
        previous = captured_inputs.single("__previous")
        manifests = captured_inputs.materialisation_manifests or run.get(
            "materialisationManifests", {}
        )
        snapshots = captured_inputs.value_snapshots
        if run.get("reuseOnly"):
            await builds.incremental.reuse(run, worker, snapshots, manifests)
            return
        needed_transactions = None
        if run.get("buildMode") == "incremental":
            needed_transactions = {
                o["definition"]["transactionId"]
                for o in objects
                if o["kind"] == "measure"
                and o["objectId"] in run["dirtyMeasures"]
                and o["definition"]["measureType"] == "referenced"
            }
        required_sources = set(managed)
        if needed_transactions is not None:
            required_sources = {
                o["definition"]["sourceId"] for o in objects if o["kind"] == "dimension"
            }
            for tx in objects:
                if (
                    tx["kind"] == "transaction"
                    and tx["objectId"] in needed_transactions
                ):
                    required_sources.add(tx["definition"]["sourceId"])
                    required_sources.update(calendar_source_ids(objects, tx))
        objects_by_id = {document["objectId"]: document for document in objects}
        await builds.managed.input_verifier.verify(
            objects,
            {
                source_id: artifact
                for source_id, artifact in managed.items()
                if source_id in required_sources
            },
            managed_definitions,
        )
        await builds.storage.ensure_inputs(
            [
                *populations.values(),
                *snapshots.values(),
            ]
        )
        edit_document = next(
            (d["document"] for d in docs if d["inputId"] == "__edit"), None
        )
        if run.get("isEdit"):
            from ..engine.measure_edits import MeasureEditCompiler
            from ..schemas.planning_view import PlanningViewEdit

            if edit_document is None:
                raise Conflict(
                    "The edit job context is missing. Refresh and submit a new edit."
                )
            await builds.storage.ensure_inputs(list(snapshots.values()))
            compiler = MeasureEditCompiler(
                builds.settings.clickhouse_database,
                objects,
                populations,
                managed,
                run["resourceId"],
                snapshots,
                PlanningViewEdit.model_validate(edit_document),
            )
        else:
            resolution_profiles = await prepare_resolution_profiles(
                builds.executor.warehouse,
                [
                    o
                    for o in objects
                    if o["kind"] != "transaction"
                    or needed_transactions is None
                    or o["objectId"] in needed_transactions
                ],
                managed,
                builds.settings.resolution_max_profiles,
                builds.executor.execution_settings(),
            )
            compiler_class = (
                IncrementalMeasureCompiler
                if run.get("buildMode") == "incremental"
                else MeasureCompiler
            )
            compiler = compiler_class(
                builds.settings.clickhouse_database,
                objects,
                populations,
                managed,
                run["resourceId"],
                previous,
                resolution_profiles,
                builds.settings.resolution_max_rows,
                **(
                    {
                        "snapshots": snapshots,
                        "manifests": manifests,
                    }
                    if run.get("buildMode") == "incremental"
                    else {}
                ),
            )
        compiler.max_nodes = builds.settings.measure_max_nodes
        outcomes = []
        order = [
            run["resourceId"],
            *(
                planning_level_id
                for planning_level_id in populations
                if planning_level_id != run["resourceId"]
            ),
        ]
        for planning_level_id in order:
            level_started = perf_counter()
            compiled, unavailable = compiler.compile_level(planning_level_id)
            if (
                run.get("isEdit")
                and set(snapshots[planning_level_id].get("measureColumns", {}))
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
            step = "values_" + planning_level_id
            await worker.step(
                run,
                step,
                "running",
                planHash=digest(compiled.sql),
                compilerVersion=compiled.compiler_version,
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
                "resourceId": planning_level_id,
                "kind": "table",
                "state": "candidate",
                "createdAt": now(),
                "inputHash": run["inputHash"],
                "attempt": run["fence"],
            }

            async def record(repo):
                await worker.fenced(repo, run)
                artifact.update(
                    await builds.storage.reserve(
                        repo, artifact, objects_by_id[planning_level_id]["name"]
                    )
                )
                await metadata_repositories(repo).artifacts.insert(artifact)

            await builds.store.transaction(record)
            keys = tuple(compiler.keys(planning_level_id))
            plan = PlanningPlan(None, compiled.schema, keys, (), (), (), False)
            outcome = await builds.executor.build(artifact, compiled, plan)
            if outcome["rowCount"] != populations[planning_level_id]["rowCount"]:
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
                "levelId": planning_level_id,
                "generationId": run["runId"],
                "numericPolicy": MEASURE_TYPE_POLICY,
                "materialisationManifest": manifests.get(planning_level_id),
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
                "populationArtifactId": populations[planning_level_id]["artifactId"],
                "compilerVersion": compiled.compiler_version,
                "derivations": [
                    {
                        "measureId": n.measure_id,
                        "planningLevelId": n.level_id,
                        "operation": n.operation,
                    }
                    for n in compiler.nodes
                    if n.level_id == planning_level_id
                ],
            }
            outcomes.append(populated)
            await worker.step(
                run,
                step,
                "succeeded",
                rowCount=outcome["rowCount"],
                artifactId=aid,
                reusedMeasures=sorted(
                    {
                        n.measure_id
                        for n in compiler.nodes
                        if n.level_id == planning_level_id and n.operation == "reuse"
                    }
                ),
                timingsMs={
                    "compile": round((compiled_at - level_started) * 1000, 3),
                    "preChecks": round(prechecks_ms, 3),
                    "tableBuild": outcome.get("buildTimingsMs", {}),
                    "postChecks": round(postchecks_ms, 3),
                    "total": round((perf_counter() - level_started) * 1000, 3),
                },
            )

        candidate = MeasureBuildCandidate(outcomes, populations, snapshots, previous)
        await self.activation.activate(run, worker, candidate)


class MeasureBuildActivation:
    def __init__(self, builds):
        self.builds = builds

    async def activate(self, run, worker, candidate: MeasureBuildCandidate):
        builds = self.builds
        outcomes, populations, snapshots, previous = (
            candidate.artifacts,
            candidate.populations,
            candidate.snapshots,
            candidate.previous,
        )

        async def activate(repo):
            current = await worker.fenced(repo, run)
            (
                revision_document,
                _,
                _,
                _,
                _,
                _,
                fingerprint,
            ) = await builds.capture_build_inputs(
                repo, run["modelId"], run["revisionId"]
            )
            builds.check_version(revision_document, run["revisionVersion"])
            if revision_document["approvalStatus"] != "draft":
                raise ImmutableRevision()
            if fingerprint != run["inputHash"]:
                raise Conflict("Measure inputs changed during the build.")
            if run.get("buildMode") == "incremental":
                await builds.incremental.check_snapshots(repo, run, snapshots)
            previous_binding = await metadata_repositories(repo).bindings.find(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    resource_kind="measureValues",
                    resource_id=run["resourceId"],
                )
            )
            if (previous_binding or {}).get("generationId") != run.get(
                "previousGenerationId", (previous or {}).get("generationId")
            ):
                raise Conflict(
                    "Another value generation was activated. Refresh and retry."
                )
            for artifact in outcomes:
                identity = {
                    **scope(run["modelId"], run["revisionId"]),
                    "resourceKind": "measureValues",
                    "resourceId": artifact["levelId"],
                }
                binding = {
                    **identity,
                    "artifactId": artifact["artifactId"],
                    "definitionHash": run["inputHash"],
                    "inputHash": run["inputHash"],
                    "generationId": run["runId"],
                    "builtAt": now(),
                }
                await metadata_repositories(repo).activation.bind(identity, binding)
                await metadata_repositories(repo).artifacts.replace(
                    ArtifactLookup(artifact_id=artifact["artifactId"]), artifact
                )
            old_bindings = await metadata_repositories(repo).bindings.list(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    resource_kind="measureValues",
                ),
                order_by="resourceId",
                limit=builds.max_objects + 1,
            )
            if len(old_bindings) > builds.max_objects:
                raise Conflict("Measure bindings exceed the metadata object limit.")
            for old in old_bindings:
                if old["resourceId"] not in populations:
                    await metadata_repositories(repo).bindings.delete(
                        BindingLookup(
                            model_id=run["modelId"],
                            revision_id=run["revisionId"],
                            resource_kind="measureValues",
                            resource_id=old["resourceId"],
                        )
                    )
            if run.get("isEdit"):
                await metadata_repositories(repo).inputs.delete(
                    RunInputLookup(run_id=run["runId"], input_id="__edit")
                )
            await builds.touch_revision(repo, run["modelId"], run["revisionId"])
            await metadata_repositories(repo).activation.complete(
                run, current, generationId=run["runId"]
            )

        await builds.store.transaction(activate)
