from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ObjectLookup,
    RevisionLookup,
    RunLookup,
)
from uuid import uuid4

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..domain.measure_policies import MEASURE_TYPE_POLICY
from ..engine.captured_inputs import MeasureBuildInputs
from ..engine.contracts import digest
from ..engine.date_range_resolution import calendar_source_ids
from ..engine.incremental import (
    INCREMENTAL_POLICY,
    MaterialisationManifest,
)
from ..engine.measure_compiler import (
    MATERIALISATION_POLICY,
    MEASURE_COMPILER_VERSION,
)
from ..engine.planning_executor import PlanningTableExecutor
from ..engine.relationships import LevelRelationshipAnalyzer
from ..engine.table_storage import STORAGE_POLICY
from ..engine.transaction_resolution import (
    RESOLUTION_POLICY,
)
from .base import Service, now, scope
from .incremental_builds import IncrementalBuildCoordinator


class MeasureBuildService(Service):
    """Build all compatible measure representations; activate one coherent revision generation."""

    def __init__(self, store, settings, planning, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.planning, self.managed = settings, planning, managed
        self.incremental = IncrementalBuildCoordinator(self)
        self.storage = planning.storage
        self.executor = PlanningTableExecutor(managed.executor.warehouse, settings)
        self.executor.storage = self.storage
        from ..engine.measure_builds import MeasureBuildHandler

        self.handler = MeasureBuildHandler(self)

    async def capture_build_inputs(self, repo, model, revision) -> MeasureBuildInputs:
        rev = await metadata_repositories(repo).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        if not rev["basePlanningLevelId"]:
            raise MetadataError(
                "base_required",
                "Select the finest Base planning level before building measures.",
            )
        objects = await self.objects(repo, model, revision)
        self.validate(
            await self.effective(repo, model, revision, objects),
            rev["basePlanningLevelId"],
        )
        levels = [o for o in objects if o["kind"] == "planningLevel"]
        populations, managed, managed_defs, bindings = {}, {}, {}, {}
        for level in levels:
            captured = await self.planning.capture_population_inputs(
                repo, model, revision, level, objects
            )
            binding = await metadata_repositories(repo).bindings.require(
                BindingLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="planningLevel",
                    resource_id=level["objectId"],
                ),
                "Ready planning table; build all planning populations first",
            )
            if (
                binding["definitionHash"] != self.planning.definition_hash(level)
                or binding["inputHash"] != captured["inputHash"]
            ):
                raise Conflict(
                    "A planning population needs rebuilding before measures can be built."
                )
            artifact = await metadata_repositories(repo).artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]),
                "Planning population artifact",
            )
            if artifact["state"] != "ready":
                raise Conflict("A planning population artifact is not ready.")
            populations[level["objectId"]] = artifact
            bindings["level:" + level["objectId"]] = binding
            for sid, definition in captured["managed"].items():
                managed_defs[sid] = definition
                managed[sid] = captured["relations"][sid]
                bindings["source:" + sid] = captured["bindings"][sid]
        resolution_sources = set()
        for tx in (o for o in objects if o["kind"] == "transaction"):
            resolution_sources.add(tx["definition"]["sourceId"])
            resolution_sources.update(calendar_source_ids(objects, tx))
        objects_by_id = {document["objectId"]: document for document in objects}
        for source_id in resolution_sources:
            source_snapshot = await self.planning.input_capture.capture_source(
                repo, model, revision, objects_by_id[source_id]
            )
            managed_defs[source_id] = source_snapshot.definition
            managed[source_id] = source_snapshot.artifact
            bindings["source:" + source_id] = source_snapshot.binding
        fingerprint = digest(
            {
                "resolutionPolicy": RESOLUTION_POLICY,
                "storagePolicy": STORAGE_POLICY,
                "materialisationPolicy": MATERIALISATION_POLICY,
                "numericPolicy": MEASURE_TYPE_POLICY,
                "base": rev["basePlanningLevelId"],
                "objects": {o["objectId"]: o["version"] for o in objects},
                "artifacts": {k: b["artifactId"] for k, b in bindings.items()},
                "managed": {k: v["definitionHash"] for k, v in managed_defs.items()},
            }
        )
        return MeasureBuildInputs(
            rev, objects, populations, managed, managed_defs, bindings, fingerprint
        )

    async def submit(self, model, revision, expected, key, edit=None, mode="full"):
        if mode not in ("full", "incremental"):
            raise MetadataError(
                "invalid_build_mode", "Choose incremental or full materialisation.", 400
            )
        if not key or len(key) > 128:
            raise MetadataError(
                "invalid_idempotency_key",
                "Supply an Idempotency-Key of 1 to 128 characters.",
                400,
            )
        request = ["measureSet", expected, edit.model_dump() if edit else None]
        if mode == "incremental" and edit is None:
            request.append("incremental")
        request_hash = digest(request)

        async def operation(repo):
            query = scope(model, revision)
            previous = await metadata_repositories(repo).runs.find(
                RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
            )
            if previous:
                if previous["requestHash"] != request_hash:
                    raise Conflict("This idempotency key belongs to another request.")
                return self.managed.public_run(previous)
            (
                rev,
                objects,
                populations,
                managed,
                defs,
                bindings,
                fingerprint,
            ) = await self.capture_build_inputs(repo, model, revision)
            self.check_version(rev, expected)
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            if not any(o["kind"] == "measure" for o in objects):
                raise MetadataError(
                    "no_measures", "Create measures before building values."
                )
            run_id = "run_" + uuid4().hex
            old = await metadata_repositories(repo).bindings.find(
                BindingLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="measureValues",
                    resource_id=rev["basePlanningLevelId"],
                )
            )
            previous_artifact = (
                await metadata_repositories(repo).artifacts.find(
                    ArtifactLookup(artifact_id=old["artifactId"])
                )
                if old
                else None
            )
            manifests = MaterialisationManifest(
                objects, populations, managed, rev["basePlanningLevelId"]
            ).all()
            snapshots, reuse_only, dirty = {}, False, set()
            if mode == "incremental" and edit is None:
                snapshots, reuse_only, dirty = await self.incremental.prepare(
                    repo,
                    model,
                    revision,
                    rev["basePlanningLevelId"],
                    populations,
                    manifests,
                    previous_artifact,
                )
            if edit is not None:
                from ..engine.measure_edits import MeasureEditPlanner
                from ..engine.planning_view import PlanningViewPlanner

                if (
                    previous_artifact is None
                    or previous_artifact.get("generationId") != edit.generationId
                ):
                    raise Conflict(
                        "The displayed values changed. Refresh the Planning View before editing."
                    )
                fields = PlanningViewPlanner(
                    objects, rev["basePlanningLevelId"]
                ).fields(edit.view)
                for lid in populations:
                    binding = await metadata_repositories(repo).bindings.require(
                        BindingLookup(
                            model_id=model,
                            revision_id=revision,
                            resource_kind="measureValues",
                            resource_id=lid,
                        ),
                        "Current values; build the network first",
                    )
                    artifact = await metadata_repositories(repo).artifacts.require(
                        ArtifactLookup(artifact_id=binding["artifactId"]),
                        "Current values",
                    )
                    if (
                        binding["inputHash"] != fingerprint
                        or artifact["state"] != "ready"
                        or artifact["generationId"] != edit.generationId
                        or artifact["populationArtifactId"]
                        != populations[lid]["artifactId"]
                    ):
                        raise Conflict(
                            "The network is stale or incomplete. Build it and refresh before editing."
                        )
                    snapshots[lid] = artifact
                MeasureEditPlanner(
                    objects, rev["basePlanningLevelId"], fields
                ).validate(edit, snapshots)
            run = {
                **query,
                "runId": run_id,
                "resourceKind": "measureSet",
                "resourceId": rev["basePlanningLevelId"],
                "requestHash": request_hash,
                "idempotencyKey": key,
                "revisionVersion": rev["version"],
                "inputHash": fingerprint,
                "storagePolicy": STORAGE_POLICY,
                "state": "queued",
                "fence": 0,
                "leaseUntil": now(),
                "createdAt": now(),
                "updatedAt": now(),
                "error": None,
                "isEdit": edit is not None,
                "buildMode": "edit" if edit is not None else mode,
                "reuseOnly": reuse_only,
                "incrementalPolicy": INCREMENTAL_POLICY,
                "incrementalCompilerVersion": MEASURE_COMPILER_VERSION,
                "dirtyMeasures": sorted(dirty),
                "previousGenerationId": previous_artifact.get("generationId")
                if previous_artifact
                else None,
            }
            await metadata_repositories(repo).runs.insert(run)
            # Keep manifests per level to stay below MongoDB's single-document limit
            # as the measure × planning-level matrix grows.
            for lid, manifest in manifests.items():
                await metadata_repositories(repo).inputs.insert(
                    {
                        "runId": run_id,
                        "inputId": "__manifest_" + lid,
                        "document": manifest,
                    }
                )
            for obj in objects:
                await metadata_repositories(repo).inputs.insert(
                    {"runId": run_id, "inputId": obj["objectId"], "document": obj}
                )
            for lid, artifact in populations.items():
                await metadata_repositories(repo).inputs.insert(
                    {
                        "runId": run_id,
                        "inputId": "__population_" + lid,
                        "document": artifact,
                    }
                )
            for sid, artifact in managed.items():
                await metadata_repositories(repo).inputs.insert(
                    {
                        "runId": run_id,
                        "inputId": "__managed_" + sid,
                        "document": {"artifact": artifact, "definition": defs[sid]},
                    }
                )
            if previous_artifact:
                await metadata_repositories(repo).inputs.insert(
                    {
                        "runId": run_id,
                        "inputId": "__previous",
                        "document": previous_artifact,
                    }
                )
            if edit is not None:
                await metadata_repositories(repo).inputs.insert(
                    {
                        "runId": run_id,
                        "inputId": "__edit",
                        "document": edit.model_dump(),
                    }
                )
            for lid, snapshot in snapshots.items():
                await metadata_repositories(repo).inputs.insert(
                    {
                        "runId": run_id,
                        "inputId": "__values_" + lid,
                        "document": snapshot,
                    }
                )
            pinned = {
                a["artifactId"]: a
                for a in [
                    *populations.values(),
                    *managed.values(),
                    *snapshots.values(),
                    *([previous_artifact] if previous_artifact else []),
                ]
            }
            for aid in pinned:
                artifact = await metadata_repositories(repo).artifacts.require(
                    ArtifactLookup(artifact_id=aid), "Input artifact"
                )
                if artifact["state"] != "ready":
                    raise Conflict(
                        "A measure input was retired; reload before retrying."
                    )
                await metadata_repositories(repo).artifacts.replace(
                    ArtifactLookup(artifact_id=aid),
                    {**artifact, "pinVersion": artifact.get("pinVersion", 0) + 1},
                )
                await metadata_repositories(repo).pins.insert(
                    {"runId": run_id, "artifactId": aid}
                )
            return self.managed.public_run(run)

        try:
            return await self.store.transaction(operation)
        except Conflict:
            previous = await metadata_repositories(self.store.repository).runs.find(
                RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
            )
            if previous and previous["requestHash"] == request_hash:
                return self.managed.public_run(previous)
            raise

    async def execute_run(self, run, worker):
        await self.handler.execute_run(run, worker)

    async def describe_level(self, repo, model, revision, level_id):
        """Explain structural materialisation from the same relationship rules as execution."""
        objects = await self.objects(repo, model, revision)
        by_id = {o["objectId"]: o for o in objects}
        rev = await metadata_repositories(repo).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        base = rev["basePlanningLevelId"]
        analyzer = LevelRelationshipAnalyzer(objects)
        items = []
        for measure in (o for o in objects if o["kind"] == "measure"):
            d = measure["definition"]
            native = d["nativePlanningLevelId"]
            direct = analyzer.analyze(native, level_id)["relationship"]
            base_to_target = analyzer.analyze(base, level_id)["relationship"]
            base_to_native = analyzer.analyze(base, native)["relationship"]
            reachable = direct != "incompatible" or (
                base_to_native in ("same", "equivalent", "roll_up")
                and base_to_target in ("same", "equivalent", "roll_up")
            )
            if d.get("calculationType") == "AggregateFirst" and reachable:
                operation = "Recalculate formula"
                explanation = (
                    "Resolve dependencies at this planning level, then evaluate "
                    + d["formula"]["displayText"]
                    + "."
                )
            elif level_id == base and native != base and direct == "roll_down":
                operation = "Disaggregate to Base"
                explanation = "Initialise matching Base rows from aggregation: split SUM axes equally and repeat other axes, in reverse aggregation order. The configured disaggregation policy applies to edits only."
            elif (
                level_id != base
                and base_to_target in ("roll_up", "equivalent")
                and base_to_native != "incompatible"
            ):
                operation = "Aggregate from Base"
                explanation = "Aggregate Base values using this measure's policies, including when returning to its native level."
            elif level_id == native:
                operation = "Load at native level"
                explanation = (
                    "Aggregate transactions by all native keys and left join the native planning population."
                    if d["measureType"] == "referenced"
                    else "Evaluate the formula at its native planning level."
                )
            elif direct in ("roll_up", "equivalent"):
                operation = "Aggregate native values"
                explanation = "Aggregate native values through the proven direct key/attribute mapping."
            else:
                operation = "Unavailable"
                explanation = "No proven key projection or dimension mapping connects this measure to the requested level."
            allocation = d.get("allocation") or {"mode": "none"}
            basis = allocation.get("basisMeasureId")
            items.append(
                {
                    "measureId": measure["objectId"],
                    "name": measure["name"],
                    "nativeLevelName": by_id[native]["name"],
                    "operation": operation,
                    "explanation": explanation,
                    "aggregation": d.get("aggregation"),
                    "disaggregation": {
                        **allocation,
                        "basisMeasureName": by_id[basis]["name"] if basis else None,
                    },
                }
            )
        return items

    async def status(self, model, revision, level_id, preview=False):
        repo = self.store.repository
        await metadata_repositories(repo).objects.require(
            ObjectLookup(
                model_id=model,
                revision_id=revision,
                kind="planningLevel",
                object_id=level_id,
            ),
            "Planning level",
        )
        result = {
            "state": "not_built",
            "columns": [],
            "rows": [],
            "rowCount": None,
            "unavailable": {},
            "generationId": None,
            "materialisation": await self.describe_level(
                repo, model, revision, level_id
            ),
        }
        binding = await metadata_repositories(repo).bindings.find(
            BindingLookup(
                model_id=model,
                revision_id=revision,
                resource_kind="measureValues",
                resource_id=level_id,
            )
        )
        if binding:
            artifact = await metadata_repositories(repo).artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]),
                "Measure value artifact",
            )
            result.update(
                state="ready",
                columns=[c for c in artifact["columns"] if c.get("role") != "coverage"],
                incompleteMeasures=artifact.get("incompleteMeasures", {}),
                rowCount=artifact["rowCount"],
                unavailable=artifact["unavailable"],
                generationId=binding["generationId"],
                builtAt=binding["builtAt"],
            )
            if artifact.get("numericPolicy") != MEASURE_TYPE_POLICY:
                result["state"] = "needs_rebuild"
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            if rev["approvalStatus"] == "draft":
                try:
                    *_, fingerprint = await self.capture_build_inputs(
                        repo, model, revision
                    )
                    if fingerprint != binding["inputHash"]:
                        result["state"] = "needs_rebuild"
                except MetadataError:
                    result["state"] = "needs_rebuild"
            if preview:
                await self.storage.ensure_inputs([artifact])
                warehouse = self.executor.warehouse

                result["rows"] = await warehouse.queries.preview_values(
                    artifact, result["columns"], self.executor.execution_settings()
                )
        runs = await metadata_repositories(repo).runs.list(
            RunLookup(model_id=model, revision_id=revision, resource_kind="measureSet"),
            order_by="createdAt",
            limit=1,
            newest_first=True,
        )
        if runs:
            run = runs[0]
            result["lastRun"] = self.managed.public_run(run)
            if run["state"] in ("queued", "running"):
                result["state"] = "building"
            elif not binding and run["state"] in ("failed", "cancelled", "superseded"):
                result["state"] = "failed"
        return result

    async def base_visibility(self, model, revision, native_id):
        repo = self.store.repository
        rev = await metadata_repositories(repo).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        objects = await self.effective(
            repo, model, revision, await self.objects(repo, model, revision)
        )
        analyzer = LevelRelationshipAnalyzer(objects)
        if native_id not in analyzer.levels:
            raise MetadataError(
                "not_found", "Output planning level was not found.", 404
            )
        base = rev["basePlanningLevelId"]
        relation = analyzer.analyze(native_id, base)
        return {
            "baseLevelId": base,
            "relationship": relation["relationship"],
            "reason": relation.get("reason"),
            "expansionAxes": analyzer.expansion_axes(native_id, base)
            if relation["relationship"] == "roll_down"
            else [],
        }

    async def capabilities(self, model, revision, measure_id):
        repo = self.store.repository
        objects = await self.objects(repo, model, revision)
        measure = next(
            (
                o
                for o in objects
                if o["objectId"] == measure_id and o["kind"] == "measure"
            ),
            None,
        )
        if not measure:
            return await metadata_repositories(repo).objects.require(
                ObjectLookup(
                    model_id=model,
                    revision_id=revision,
                    object_id=measure_id,
                    kind="measure",
                ),
                "Measure",
            )
        analyzer = LevelRelationshipAnalyzer(objects)
        native = measure["definition"]["nativePlanningLevelId"]
        revision_doc = await metadata_repositories(repo).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        base = revision_doc["basePlanningLevelId"]
        levels = []
        for lid in analyzer.levels:
            result = analyzer.analyze(native, lid)
            via_base = bool(
                base
                and all(
                    analyzer.analyze(base, key)["relationship"]
                    in ("same", "equivalent", "roll_up")
                    for key in (native, lid)
                )
            )
            readable = result["relationship"] != "incompatible" or via_base
            levels.append(
                {
                    "planningLevelId": lid,
                    **result,
                    "readable": readable,
                    "viaBase": via_base and result["relationship"] == "incompatible",
                    "editable": analyzer.can_edit(measure, lid),
                }
            )
        return {"measureId": measure_id, "levels": levels}
