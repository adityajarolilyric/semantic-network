from ..engine.date_range_resolution import calendar_source_ids
from ..engine.transaction_resolution import (
    RESOLUTION_POLICY,
    prepare_resolution_profiles,
)
from ..engine.table_storage import STORAGE_POLICY, owner, relation_sql
from .measure_types import MEASURE_TYPE_POLICY
from uuid import uuid4

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..engine.contracts import PlanningPlan, digest
from ..engine.measure_compiler import MeasureCompiler
from ..engine.planning_executor import PlanningTableExecutor
from ..engine.relationships import LevelRelationshipAnalyzer
from .base import Service, now, scope


class MeasureBuildService(Service):
    """Build all compatible measure representations; activate one coherent revision generation."""

    def __init__(self, store, settings, planning, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.planning, self.managed = settings, planning, managed
        self.storage = planning.storage
        self.executor = PlanningTableExecutor(managed.executor.warehouse, settings)
        self.executor.storage = self.storage

    async def capture(self, repo, model, revision):
        query = scope(model, revision)
        rev = await self.require(repo, "semantic_revisions", query, "Revision")
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
            captured = await self.planning.capture(
                repo, model, revision, level, objects
            )
            binding = await self.require(
                repo,
                "engine_artifact_bindings",
                {
                    **query,
                    "resourceKind": "planningLevel",
                    "resourceId": level["objectId"],
                },
                "Ready planning table; build all planning populations first",
            )
            if (
                binding["definitionHash"] != self.planning.definition_hash(level)
                or binding["inputHash"] != captured["inputHash"]
            ):
                raise Conflict(
                    "A planning population needs rebuilding before measures can be built."
                )
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
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
        for sid in resolution_sources:
            # Reuse managed-view readiness/snapshot resolution without adding semantic objects.
            fake = {
                "objectId": "capture",
                "definition": {
                    "population": {
                        "operation": "source",
                        "sourceId": sid,
                    },
                    "dimensionBindings": [],
                },
            }
            captured = await self.planning.capture(repo, model, revision, fake, objects)
            for sid, definition in captured["managed"].items():
                managed_defs[sid] = definition
                managed[sid] = captured["relations"][sid]
                bindings["source:" + sid] = captured["bindings"][sid]
        fingerprint = digest(
            {
                "resolutionPolicy": RESOLUTION_POLICY,
                "storagePolicy": STORAGE_POLICY,
                "numericPolicy": MEASURE_TYPE_POLICY,
                "base": rev["basePlanningLevelId"],
                "objects": {o["objectId"]: o["version"] for o in objects},
                "artifacts": {k: b["artifactId"] for k, b in bindings.items()},
                "managed": {k: v["definitionHash"] for k, v in managed_defs.items()},
            }
        )
        return rev, objects, populations, managed, managed_defs, bindings, fingerprint

    async def submit(self, model, revision, expected, key, edit=None):
        if not key or len(key) > 128:
            raise MetadataError(
                "invalid_idempotency_key",
                "Supply an Idempotency-Key of 1 to 128 characters.",
                400,
            )
        request_hash = digest(
            ["measureSet", expected, edit.model_dump() if edit else None]
        )

        async def operation(repo):
            query = scope(model, revision)
            previous = await repo.get("engine_runs", {**query, "idempotencyKey": key})
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
            ) = await self.capture(repo, model, revision)
            self.check_version(rev, expected)
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            if not any(o["kind"] == "measure" for o in objects):
                raise MetadataError(
                    "no_measures", "Create measures before building values."
                )
            run_id = "run_" + uuid4().hex
            old = await repo.get(
                "engine_artifact_bindings",
                {
                    **query,
                    "resourceKind": "measureValues",
                    "resourceId": rev["basePlanningLevelId"],
                },
            )
            previous_artifact = (
                await repo.get("engine_artifacts", {"artifactId": old["artifactId"]})
                if old
                else None
            )
            snapshots = {}
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
                    binding = await self.require(
                        repo,
                        "engine_artifact_bindings",
                        {**query, "resourceKind": "measureValues", "resourceId": lid},
                        "Current values; build the network first",
                    )
                    artifact = await self.require(
                        repo,
                        "engine_artifacts",
                        {"artifactId": binding["artifactId"]},
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
                "previousGenerationId": previous_artifact.get("generationId")
                if previous_artifact
                else None,
            }
            await repo.insert("engine_runs", run)
            for obj in objects:
                await repo.insert(
                    "engine_run_inputs",
                    {"runId": run_id, "inputId": obj["objectId"], "document": obj},
                )
            for lid, artifact in populations.items():
                await repo.insert(
                    "engine_run_inputs",
                    {
                        "runId": run_id,
                        "inputId": "__population_" + lid,
                        "document": artifact,
                    },
                )
            for sid, artifact in managed.items():
                await repo.insert(
                    "engine_run_inputs",
                    {
                        "runId": run_id,
                        "inputId": "__managed_" + sid,
                        "document": {"artifact": artifact, "definition": defs[sid]},
                    },
                )
            if previous_artifact:
                await repo.insert(
                    "engine_run_inputs",
                    {
                        "runId": run_id,
                        "inputId": "__previous",
                        "document": previous_artifact,
                    },
                )
            if edit is not None:
                await repo.insert(
                    "engine_run_inputs",
                    {
                        "runId": run_id,
                        "inputId": "__edit",
                        "document": edit.model_dump(),
                    },
                )
                for lid, snapshot in snapshots.items():
                    await repo.insert(
                        "engine_run_inputs",
                        {
                            "runId": run_id,
                            "inputId": "__values_" + lid,
                            "document": snapshot,
                        },
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
                artifact = await self.require(
                    repo, "engine_artifacts", {"artifactId": aid}, "Input artifact"
                )
                if artifact["state"] != "ready":
                    raise Conflict(
                        "A measure input was retired; reload before retrying."
                    )
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": aid},
                    {**artifact, "pinVersion": artifact.get("pinVersion", 0) + 1},
                )
                await repo.insert(
                    "engine_artifact_pins", {"runId": run_id, "artifactId": aid}
                )
            return self.managed.public_run(run)

        try:
            return await self.store.transaction(operation)
        except Conflict:
            previous = await self.store.repository.get(
                "engine_runs", {**scope(model, revision), "idempotencyKey": key}
            )
            if previous and previous["requestHash"] == request_hash:
                return self.managed.public_run(previous)
            raise

    async def execute_run(self, run, worker):
        if run.get("storagePolicy") != STORAGE_POLICY:
            raise Conflict(
                "This build predates the table-storage upgrade. Submit a new build."
            )
        docs = await self.store.repository.list(
            "engine_run_inputs",
            {"runId": run["runId"]},
            key="inputId",
            limit=self.max_objects * 4 + 3,
        )
        objects = [d["document"] for d in docs if not d["inputId"].startswith("__")]
        populations = {
            d["inputId"][13:]: d["document"]
            for d in docs
            if d["inputId"].startswith("__population_")
        }
        managed = {
            d["inputId"][10:]: d["document"]["artifact"]
            for d in docs
            if d["inputId"].startswith("__managed_")
        }
        defs = {
            d["inputId"][10:]: d["document"]["definition"]
            for d in docs
            if d["inputId"].startswith("__managed_")
        }
        previous = next(
            (d["document"] for d in docs if d["inputId"] == "__previous"), None
        )
        by_id = {o["objectId"]: o for o in objects}
        for sid, artifact in managed.items():
            source = by_id[sid]
            await self.managed.executor.inspect(source)
            plan = self.managed.planner.plan(
                source, self.managed.input_definition(defs[sid]["definition"])
            )
            compiled = self.managed.compiler.compile(plan)
            await self.managed.executor.verify(artifact, compiled)
            await self.managed.executor.checks(compiled)
        await self.storage.ensure_inputs(
            [*populations.values(), *([previous] if previous else [])]
        )
        edit_document = next(
            (d["document"] for d in docs if d["inputId"] == "__edit"), None
        )
        snapshots = {
            d["inputId"][9:]: d["document"]
            for d in docs
            if d["inputId"].startswith("__values_")
        }
        if run.get("isEdit"):
            from ..schemas.planning_view import PlanningViewEdit
            from ..engine.measure_edits import MeasureEditCompiler

            if edit_document is None:
                raise Conflict(
                    "The edit job context is missing. Refresh and submit a new edit."
                )
            await self.storage.ensure_inputs(list(snapshots.values()))
            compiler = MeasureEditCompiler(
                self.settings.clickhouse_database,
                objects,
                populations,
                managed,
                run["resourceId"],
                snapshots,
                PlanningViewEdit.model_validate(edit_document),
            )
        else:
            resolution_profiles = await prepare_resolution_profiles(
                self.executor.warehouse,
                objects,
                managed,
                self.settings.resolution_max_profiles,
                self.executor.execution_settings(),
            )
            compiler = MeasureCompiler(
                self.settings.clickhouse_database,
                objects,
                populations,
                managed,
                run["resourceId"],
                previous,
                resolution_profiles,
                self.settings.resolution_max_rows,
            )
        compiler.max_nodes = self.settings.measure_max_nodes
        outcomes = []
        order = [
            run["resourceId"],
            *(lid for lid in populations if lid != run["resourceId"]),
        ]
        for lid in order:
            compiled, unavailable = compiler.compile_level(lid)
            if (
                run.get("isEdit")
                and set(snapshots[lid].get("measureColumns", {})) & unavailable.keys()
            ):
                raise MetadataError(
                    "edit_dependency_unavailable",
                    "An existing measure cannot be recomputed at this level. No edit was activated.",
                )
            if len(compiled.sql.encode()) > self.settings.measure_max_sql_bytes:
                raise MetadataError(
                    "measure_sql_limit",
                    "Compiled measure graph exceeds the configured SQL-size limit.",
                )
            step = "values_" + lid
            await worker.step(
                run,
                step,
                "running",
                planHash=digest(compiled.sql),
                compilerVersion=compiled.compiler_version,
                unavailable=unavailable,
            )
            await self.executor.checks(compiled)
            aid = "artifact_" + uuid4().hex
            artifact = {
                **scope(run["modelId"], run["revisionId"]),
                "artifactId": aid,
                "runId": run["runId"],
                "resourceKind": "measureValues",
                "resourceId": lid,
                "kind": "table",
                "state": "candidate",
                "createdAt": now(),
                "inputHash": run["inputHash"],
                "attempt": run["fence"],
            }

            async def record(repo):
                await worker.fenced(repo, run)
                artifact.update(
                    await self.storage.reserve(repo, artifact, by_id[lid]["name"])
                )
                await repo.insert("engine_artifacts", artifact)

            await self.store.transaction(record)
            keys = tuple(compiler.keys(lid))
            plan = PlanningPlan(None, compiled.schema, keys, (), (), (), False)
            outcome = await self.executor.build(artifact, compiled, plan)
            if outcome["rowCount"] != populations[lid]["rowCount"]:
                raise MetadataError(
                    "measure_cardinality",
                    "Measure merging changed the planning population row count.",
                )
            await self.executor.checks(compiled)
            populated = {
                **artifact,
                **outcome,
                "state": "ready",
                "builtAt": now(),
                "levelId": lid,
                "generationId": run["runId"],
                "numericPolicy": MEASURE_TYPE_POLICY,
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
                "unavailable": unavailable,
                "populationArtifactId": populations[lid]["artifactId"],
                "compilerVersion": compiled.compiler_version,
                "derivations": [
                    {
                        "measureId": n.measure_id,
                        "planningLevelId": n.level_id,
                        "operation": n.operation,
                    }
                    for n in compiler.nodes
                    if n.level_id == lid
                ],
            }
            outcomes.append(populated)
            await worker.step(
                run, step, "succeeded", rowCount=outcome["rowCount"], artifactId=aid
            )

        async def activate(repo):
            current = await worker.fenced(repo, run)
            rev, _, _, _, _, _, fingerprint = await self.capture(
                repo, run["modelId"], run["revisionId"]
            )
            self.check_version(rev, run["revisionVersion"])
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            if fingerprint != run["inputHash"]:
                raise Conflict("Measure inputs changed during the build.")
            previous_binding = await repo.get(
                "engine_artifact_bindings",
                {
                    **scope(run["modelId"], run["revisionId"]),
                    "resourceKind": "measureValues",
                    "resourceId": run["resourceId"],
                },
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
                if await repo.get("engine_artifact_bindings", identity):
                    await repo.replace("engine_artifact_bindings", identity, binding)
                else:
                    await repo.insert("engine_artifact_bindings", binding)
                await repo.replace(
                    "engine_artifacts", {"artifactId": artifact["artifactId"]}, artifact
                )
            old_bindings = await repo.list(
                "engine_artifact_bindings",
                {
                    **scope(run["modelId"], run["revisionId"]),
                    "resourceKind": "measureValues",
                },
                key="resourceId",
                limit=self.max_objects + 1,
            )
            if len(old_bindings) > self.max_objects:
                raise Conflict("Measure bindings exceed the metadata object limit.")
            for old in old_bindings:
                if old["resourceId"] not in populations:
                    await repo.delete(
                        "engine_artifact_bindings",
                        {
                            **scope(run["modelId"], run["revisionId"]),
                            "resourceKind": "measureValues",
                            "resourceId": old["resourceId"],
                        },
                    )
            if run.get("isEdit"):
                await repo.delete(
                    "engine_run_inputs", {"runId": run["runId"], "inputId": "__edit"}
                )
            await self.touch_revision(repo, run["modelId"], run["revisionId"])
            await repo.replace(
                "engine_runs",
                {"runId": run["runId"], "fence": run["fence"]},
                {
                    **current,
                    "state": "succeeded",
                    "updatedAt": now(),
                    "generationId": run["runId"],
                },
            )

        await self.store.transaction(activate)

    async def describe_level(self, repo, model, revision, level_id):
        """Explain structural materialisation from the same relationship rules as execution."""
        objects = await self.objects(repo, model, revision)
        by_id = {o["objectId"]: o for o in objects}
        rev = await self.require(
            repo, "semantic_revisions", scope(model, revision), "Revision"
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
                explanation = "Distribute native values to matching Base rows using the configured disaggregation policy."
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
        query = scope(model, revision)
        await self.require(
            repo,
            "semantic_objects",
            {**query, "kind": "planningLevel", "objectId": level_id},
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
        binding = await repo.get(
            "engine_artifact_bindings",
            {**query, "resourceKind": "measureValues", "resourceId": level_id},
        )
        if binding:
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
                "Measure value artifact",
            )
            result.update(
                state="ready",
                columns=artifact["columns"],
                rowCount=artifact["rowCount"],
                unavailable=artifact["unavailable"],
                generationId=binding["generationId"],
                builtAt=binding["builtAt"],
            )
            if artifact.get("numericPolicy") != MEASURE_TYPE_POLICY:
                result["state"] = "needs_rebuild"
            rev = await self.require(repo, "semantic_revisions", query, "Revision")
            if rev["approvalStatus"] == "draft":
                try:
                    *_, fingerprint = await self.capture(repo, model, revision)
                    if fingerprint != binding["inputHash"]:
                        result["state"] = "needs_rebuild"
                except MetadataError:
                    result["state"] = "needs_rebuild"
            if preview:
                await self.storage.ensure_inputs([artifact])
                warehouse = self.executor.warehouse

                def read(connect):
                    client = connect()
                    info = warehouse.table_info(client, artifact["physicalName"])
                    if not info or info[0] != "MergeTree" or info[1] != owner(artifact):
                        raise Conflict("The built measure table is missing or changed.")
                    rows = client.query(
                        relation_sql(warehouse, artifact) + " ORDER BY ALL LIMIT 100",
                        settings=self.executor.execution_settings(),
                    ).result_rows
                    return [[warehouse.cell(v) for v in row] for row in rows]

                result["rows"] = await warehouse.run(read)
        runs = await repo.list(
            "engine_runs",
            {**query, "resourceKind": "measureSet"},
            key="createdAt",
            limit=1,
            descending=True,
        )
        if runs:
            run = runs[0]
            result["lastRun"] = self.managed.public_run(run)
            if run["state"] in ("queued", "running"):
                result["state"] = "building"
            elif not binding and run["state"] in ("failed", "cancelled", "superseded"):
                result["state"] = "failed"
        return result

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
            return await self.require(
                repo,
                "semantic_objects",
                {**scope(model, revision), "objectId": measure_id, "kind": "measure"},
                "Measure",
            )
        analyzer = LevelRelationshipAnalyzer(objects)
        native = measure["definition"]["nativePlanningLevelId"]
        revision_doc = await self.require(
            repo, "semantic_revisions", scope(model, revision), "Revision"
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
