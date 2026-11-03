from ..engine.table_storage import TableStorage, STORAGE_POLICY, owner, relation_sql
from uuid import uuid4

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..engine.contracts import digest
from ..engine.planning import PlanningLevelPlanner
from ..engine.planning_compiler import PlanningLevelCompiler
from ..engine.planning_executor import PlanningTableExecutor
from .base import Service, now, scope


class PlanningBuildService(Service):
    def __init__(self, store, settings, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.managed = settings, managed
        self.planner = PlanningLevelPlanner()
        self.compiler = PlanningLevelCompiler(settings.clickhouse_database)
        self.storage = TableStorage(store, managed.executor.warehouse)
        self.executor = PlanningTableExecutor(managed.executor.warehouse, settings)
        self.executor.storage = self.storage

    @staticmethod
    def definition_hash(level):
        return digest({"name": level["name"], "definition": level["definition"]})

    async def capture(self, repo, model, revision, level, objects, seen=None):
        by_id = {o["objectId"]: o for o in objects}
        seen = set() if seen is None else seen
        if level["objectId"] in seen:
            raise Conflict("Planning dependencies contain a cycle.")
        seen = seen | {level["objectId"]}
        relations, versions, bindings, managed_inputs = {}, {}, {}, {}
        definition = level["definition"]
        population = definition["population"]
        source_ids = set()
        if population["operation"] == "source":
            source_ids.add(population["sourceId"])
        elif population["operation"] == "cartesian_product":
            source_ids |= {f["sourceId"] for f in population["factors"]}
        else:
            parent = by_id[population["sourcePlanningLevelId"]]
            parent_capture = await self.capture(
                repo, model, revision, parent, objects, seen
            )
            identity = {
                **scope(model, revision),
                "resourceKind": "planningLevel",
                "resourceId": parent["objectId"],
            }
            binding = await self.require(
                repo,
                "engine_artifact_bindings",
                identity,
                "Ready parent planning table; build the parent first",
            )
            if (
                binding["definitionHash"] != self.definition_hash(parent)
                or binding["inputHash"] != parent_capture["inputHash"]
            ):
                raise Conflict("The parent planning table needs rebuilding first.")
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
                "Parent table artifact",
            )
            if artifact["state"] != "ready":
                raise Conflict("The parent table is not ready.")
            relations[parent["objectId"]] = artifact
            versions[parent["objectId"]] = parent["version"]
            bindings[parent["objectId"]] = binding
        for binding in definition["dimensionBindings"]:
            dim = by_id[binding["dimensionId"]]
            versions[dim["objectId"]] = dim["version"]
            source_ids.add(dim["definition"]["sourceId"])
        for sid in sorted(source_ids):
            source = by_id[sid]
            if not source["definition"]["inScope"]:
                raise Conflict(
                    "A population or dimension source is outside managed scope."
                )
            identity = {**scope(model, revision), "sourceId": sid}
            managed = await self.require(
                repo,
                "semantic_managed_tables",
                identity,
                "Initialized managed definition",
            )
            binding = await self.require(
                repo, "engine_artifact_bindings", identity, "Ready managed view"
            )
            if binding["definitionHash"] != managed["definitionHash"]:
                raise Conflict("Build the input managed view first.")
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
                "Managed artifact",
            )
            if artifact["state"] != "ready":
                raise Conflict("The input managed view is not ready.")
            relations[sid] = {**artifact, "columns": managed["definition"]["columns"]}
            versions[sid] = source["version"]
            bindings[sid] = binding
            managed_inputs[sid] = managed
        return {
            "relations": relations,
            "versions": versions,
            "bindings": bindings,
            "managed": managed_inputs,
            "inputHash": digest(
                {
                    "storagePolicy": STORAGE_POLICY,
                    "versions": versions,
                    "bindings": {k: v["artifactId"] for k, v in bindings.items()},
                    "managed": {
                        k: v["definitionHash"] for k, v in managed_inputs.items()
                    },
                }
            ),
        }

    async def submit(self, model, revision, level_id, expected, key, payload=None):
        if not key or len(key) > 128:
            raise MetadataError(
                "invalid_idempotency_key",
                "Supply an Idempotency-Key of 1 to 128 characters.",
                400,
            )
        request_hash = digest(
            ["planningLevel", level_id, expected]
            + ([payload.model_dump()] if payload else [])
        )

        async def operation(repo):
            query = scope(model, revision)
            previous = await repo.get("engine_runs", {**query, "idempotencyKey": key})
            if previous:
                if previous["requestHash"] != request_hash:
                    raise Conflict("This idempotency key belongs to another request.")
                return self.managed.public_run(previous)
            rev = await self.require(repo, "semantic_revisions", query, "Revision")
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            objects = await self.objects(repo, model, revision)
            if payload is not None:
                current = await repo.get(
                    "semantic_objects", {**query, "objectId": level_id}
                )
                if current and (
                    current["kind"] != "planningLevel" or current["version"] != expected
                ):
                    raise Conflict(
                        "This planning definition changed. Reload before previewing."
                    )
                if not current and expected is not None:
                    raise Conflict(
                        "This planning level was removed. Reload before previewing."
                    )
                if not current and len(objects) >= self.max_objects:
                    raise Conflict(
                        "The revision has reached its metadata object limit."
                    )
                changed = not current or digest(
                    {k: current[k] for k in payload.model_dump()}
                ) != digest(payload.model_dump())
                level = {
                    **payload.model_dump(),
                    **query,
                    "version": (current["version"] if current else 0) + int(changed),
                    "createdAt": current["createdAt"] if current else now(),
                    "updatedAt": now(),
                }
                objects = [o for o in objects if o["objectId"] != level_id] + [level]
            else:
                level = await self.require(
                    repo,
                    "semantic_objects",
                    {**query, "objectId": level_id, "kind": "planningLevel"},
                    "Planning level",
                )
                self.check_version(level, expected)
            self.validate(
                await self.effective(repo, model, revision, objects),
                rev["basePlanningLevelId"],
            )
            captured = await self.capture(repo, model, revision, level, objects)
            self.planner.plan(level, objects, captured["relations"])
            run_id, artifact_id = "run_" + uuid4().hex, "artifact_" + uuid4().hex
            run = {
                **query,
                "runId": run_id,
                "resourceKind": "planningLevelPreview" if payload else "planningLevel",
                "resourceId": level_id,
                "previewOnly": payload is not None,
                "candidateHash": digest(payload.model_dump()) if payload else None,
                "idempotencyKey": key,
                "requestHash": request_hash,
                "expectedVersion": expected,
                "revisionVersion": rev["version"],
                "definitionHash": self.definition_hash(level),
                "capture": {
                    k: v
                    for k, v in captured.items()
                    if k not in ("relations", "managed")
                },
                "inputHash": captured["inputHash"],
                "artifactId": artifact_id,
                "storagePolicy": STORAGE_POLICY,
                "state": "queued",
                "fence": 0,
                "leaseUntil": now(),
                "createdAt": now(),
                "updatedAt": now(),
                "error": None,
            }
            await repo.insert("engine_runs", run)
            for obj in objects:
                await repo.insert(
                    "engine_run_inputs",
                    {"runId": run_id, "inputId": obj["objectId"], "document": obj},
                )
            for sid, relation in captured["relations"].items():
                await repo.insert(
                    "engine_run_inputs",
                    {
                        "runId": run_id,
                        "inputId": "__relation_" + sid,
                        "document": {
                            "resourceId": sid,
                            "relation": relation,
                            "managed": captured["managed"].get(sid),
                        },
                    },
                )
            await repo.insert(
                "engine_artifacts",
                await self.storage.reserve(
                    repo,
                    {
                        **query,
                        "resourceKind": "planningLevel",
                        "resourceId": level_id,
                        "kind": "table",
                        "artifactId": artifact_id,
                        "runId": run_id,
                        "definitionHash": run["definitionHash"],
                        "attempt": 1,
                        "state": "candidate",
                        "createdAt": now(),
                    },
                    level["name"],
                ),
            )
            for relation in captured["relations"].values():
                artifact = await self.require(
                    repo,
                    "engine_artifacts",
                    {"artifactId": relation["artifactId"]},
                    "Input artifact",
                )
                if artifact["state"] != "ready":
                    raise Conflict("An input artifact was retired. Retry the build.")
                # Shared write serializes retirement against acquisition of a read pin.
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": artifact["artifactId"]},
                    {**artifact, "pinVersion": artifact.get("pinVersion", 0) + 1},
                )
                await repo.insert(
                    "engine_artifact_pins",
                    {"runId": run_id, "artifactId": artifact["artifactId"]},
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
        inputs = await self.store.repository.list(
            "engine_run_inputs",
            {"runId": run["runId"]},
            key="inputId",
            limit=self.max_objects * 2 + 1,
        )
        objects = [
            i["document"] for i in inputs if not i["inputId"].startswith("__relation_")
        ]
        level = next(o for o in objects if o["objectId"] == run["resourceId"])
        captured = {**run["capture"], "relations": {}, "managed": {}}
        for item in inputs:
            if item["inputId"].startswith("__relation_"):
                doc = item["document"]
                captured["relations"][doc["resourceId"]] = doc["relation"]
                if doc["managed"]:
                    captured["managed"][doc["resourceId"]] = doc["managed"]
        by_id = {o["objectId"]: o for o in objects}
        for sid, managed in captured["managed"].items():
            source = by_id[sid]
            await self.managed.executor.inspect(source)
            compiled = self.managed.compiler.compile(
                self.managed.planner.plan(
                    source, self.managed.input_definition(managed["definition"])
                )
            )
            await self.managed.executor.verify(captured["relations"][sid], compiled)
            await self.managed.executor.checks(compiled)
        await self.storage.ensure_inputs(captured["relations"].values())
        plan = self.planner.plan(level, objects, captured["relations"])
        compiled = self.compiler.compile(plan)
        estimate = await self.executor.estimate(plan, self.compiler)
        await worker.step(
            run,
            "compile",
            "succeeded",
            sql=compiled.sql,
            planHash=digest(compiled.sql),
            estimate=estimate,
            compilerVersion=compiled.compiler_version,
        )
        await worker.step(run, "checks", "running")
        await self.executor.checks(compiled)
        await worker.step(run, "checks", "succeeded")
        artifact = await self.require(
            self.store.repository,
            "engine_artifacts",
            {"artifactId": run["artifactId"]},
            "Candidate table",
        )
        await worker.step(run, "materialize", "running")
        outcome = await self.executor.build(artifact, compiled, plan)
        await self.executor.checks(compiled)
        await worker.step(run, "materialize", "succeeded", rowCount=outcome["rowCount"])

        async def activate(repo):
            current_run = await worker.fenced(repo, run)
            query = scope(run["modelId"], run["revisionId"])
            revision = await self.require(repo, "semantic_revisions", query, "Revision")
            self.check_version(revision, run["revisionVersion"])
            if revision["approvalStatus"] != "draft":
                raise ImmutableRevision()
            current_objects = await self.objects(
                repo, run["modelId"], run["revisionId"]
            )
            current = next(
                (o for o in current_objects if o["objectId"] == run["resourceId"]), None
            )
            if run.get("previewOnly"):
                if (current["version"] if current else None) != run["expectedVersion"]:
                    raise Conflict(
                        "The saved planning definition changed during preview."
                    )
                proposed = level
                proposed_objects = [
                    o for o in current_objects if o["objectId"] != proposed["objectId"]
                ] + [proposed]
                inputs = await self.capture(
                    repo, run["modelId"], run["revisionId"], proposed, proposed_objects
                )
            else:
                if not current or current["version"] != run["expectedVersion"]:
                    raise Conflict()
                inputs = await self.capture(
                    repo, run["modelId"], run["revisionId"], current, current_objects
                )
            if inputs["inputHash"] != run["inputHash"]:
                raise Conflict("Build dependencies changed during execution.")
            identity = {
                **query,
                "resourceKind": "planningLevel",
                "resourceId": run["resourceId"],
            }
            binding = {
                **identity,
                "artifactId": artifact["artifactId"],
                "definitionHash": run["definitionHash"],
                "inputHash": run["inputHash"],
                "builtAt": now(),
            }
            if not run.get("previewOnly"):
                if await repo.get("engine_artifact_bindings", identity):
                    await repo.replace("engine_artifact_bindings", identity, binding)
                else:
                    await repo.insert("engine_artifact_bindings", binding)
            await repo.replace(
                "engine_artifacts",
                {"artifactId": artifact["artifactId"]},
                {
                    **artifact,
                    **outcome,
                    "state": "ready",
                    "compilerVersion": compiled.compiler_version,
                    "dependencies": captured["bindings"],
                    "builtAt": now(),
                },
            )
            # Publication and competing materializations observe a common revision guard.
            if not run.get("previewOnly"):
                await self.touch_revision(repo, run["modelId"], run["revisionId"])
            await repo.replace(
                "engine_runs",
                {"runId": run["runId"], "fence": run["fence"]},
                {
                    **current_run,
                    "state": "succeeded",
                    "updatedAt": now(),
                    "rowCount": outcome["rowCount"],
                },
            )

        await self.store.transaction(activate)

    async def status(self, model, revision, level_id, *, preview=False):
        repo = self.store.repository
        query = scope(model, revision)
        level = await self.require(
            repo,
            "semantic_objects",
            {**query, "objectId": level_id, "kind": "planningLevel"},
            "Planning level",
        )
        binding = await repo.get(
            "engine_artifact_bindings",
            {**query, "resourceKind": "planningLevel", "resourceId": level_id},
        )
        result = {
            "state": "not_built",
            "rowCount": None,
            "columns": [],
            "rows": [],
            "builtAt": None,
        }
        runs = await repo.list(
            "engine_runs",
            {**query, "resourceKind": "planningLevel", "resourceId": level_id},
            key="createdAt",
            limit=1,
            descending=True,
        )
        active = None
        for state in ("running", "queued"):
            pending = await repo.list(
                "engine_runs",
                {
                    **query,
                    "resourceKind": "planningLevel",
                    "resourceId": level_id,
                    "state": state,
                },
                key="runId",
                limit=1,
            )
            if pending:
                active = pending[0]
                break
        if binding:
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
                "Planning table artifact",
            )
            result.update(
                state="ready",
                rowCount=artifact["rowCount"],
                columns=artifact["columns"],
                builtAt=binding["builtAt"],
            )
            objects = await self.objects(repo, model, revision)
            rev = await self.require(repo, "semantic_revisions", query, "Revision")
            if rev["approvalStatus"] == "draft":
                try:
                    current = await self.capture(repo, model, revision, level, objects)
                    if (
                        binding["definitionHash"] != self.definition_hash(level)
                        or binding["inputHash"] != current["inputHash"]
                    ):
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
                        raise Conflict(
                            "The planning table is missing or changed. Build it again in a draft."
                        )
                    data = client.query(
                        relation_sql(warehouse, artifact) + " ORDER BY ALL LIMIT 50",
                        settings=self.executor.execution_settings(),
                    )
                    return [
                        [warehouse.cell(v) for v in row] for row in data.result_rows
                    ]

                result["rows"] = await warehouse.run(read)
        if active:
            result.update(state="building", runId=active["runId"])
        elif runs and runs[-1]["state"] in ("failed", "superseded", "cancelled"):
            result["lastError"] = runs[-1].get("error")
            if not binding:
                result["state"] = "failed"
        return result

    async def schema(self, model, revision, payload):
        """Analyze the proposed public output without building or saving it."""

        async def snapshot(repo):
            await self.require(
                repo, "semantic_revisions", scope(model, revision), "Revision"
            )
            objects = await self.objects(repo, model, revision)
            candidate = {**payload.model_dump(), "version": 0}
            objects = [o for o in objects if o["objectId"] != candidate["objectId"]] + [
                candidate
            ]
            captured = await self.capture(repo, model, revision, candidate, objects)
            return candidate, objects, captured

        candidate, objects, captured = await self.store.transaction(snapshot)
        plan = self.planner.plan(candidate, objects, captured["relations"])
        return {"columns": list(plan.schema.columns)}

    async def preview(self, model, revision, payload):
        async def snapshot(repo):
            rev = await self.require(
                repo, "semantic_revisions", scope(model, revision), "Revision"
            )
            objects = await self.objects(repo, model, revision)
            candidate = {**payload.model_dump(), "version": 0}
            candidates = [
                o for o in objects if o["objectId"] != candidate["objectId"]
            ] + [candidate]
            self.validate(
                await self.effective(repo, model, revision, candidates),
                rev["basePlanningLevelId"],
            )
            capture = await self.capture(repo, model, revision, candidate, candidates)
            return candidate, candidates, capture

        level, objects, captured = await self.store.transaction(snapshot)
        await self.storage.ensure_inputs(captured["relations"].values())
        plan = self.planner.plan(level, objects, captured["relations"])
        estimate = await self.executor.estimate(plan, self.compiler)
        return {**await self.executor.preview(self.compiler.compile(plan)), **estimate}

    async def preflight(self, model, revision):
        for level in await self.objects(self.store.repository, model, revision):
            if level["kind"] == "planningLevel":
                status = await self.status(
                    model, revision, level["objectId"], preview=True
                )
                if status["state"] != "ready":
                    raise Conflict(
                        "Build all planning-level tables with current inputs before publishing."
                    )

    async def draft_preview(self, model, revision, run_id):
        from datetime import timedelta

        read_id = "read_" + uuid4().hex

        async def snapshot(repo):
            run = await self.require(
                repo,
                "engine_runs",
                {**scope(model, revision), "runId": run_id},
                "Preview build",
            )
            if not run.get("previewOnly") or run["state"] != "succeeded":
                raise Conflict("The preview build is not ready.")
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": run["artifactId"]},
                "Preview generation",
            )
            if artifact["state"] != "ready":
                raise Conflict(
                    "This preview has expired. Preview the configuration again."
                )
            await repo.replace(
                "engine_artifacts",
                {"artifactId": artifact["artifactId"]},
                {**artifact, "pinVersion": artifact.get("pinVersion", 0) + 1},
            )
            await repo.insert(
                "engine_read_leases",
                {
                    "readId": read_id,
                    "artifactId": artifact["artifactId"],
                    "expiresAt": now() + timedelta(seconds=180),
                },
            )
            return artifact

        artifact = await self.store.transaction(snapshot)
        try:
            await self.storage.ensure_inputs([artifact])
            warehouse = self.executor.warehouse

            def read(connect):
                result = connect().query(
                    relation_sql(warehouse, artifact) + " ORDER BY ALL LIMIT 50",
                    settings=self.executor.execution_settings(),
                )
                return [[warehouse.cell(v) for v in row] for row in result.result_rows]

            return {
                "state": "preview",
                "rowCount": artifact["rowCount"],
                "columns": artifact["columns"],
                "rows": await warehouse.run(read),
            }
        finally:
            await self.store.transaction(
                lambda repo: repo.delete(
                    "engine_read_leases",
                    {"readId": read_id, "artifactId": artifact["artifactId"]},
                )
            )

    async def commit_draft(self, model, revision, run_id, payload):
        query = scope(model, revision)

        async def commit(repo):
            rev = await self.require(repo, "semantic_revisions", query, "Revision")
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            run = await self.require(
                repo, "engine_runs", {**query, "runId": run_id}, "Preview build"
            )
            if (
                not run.get("previewOnly")
                or run["state"] != "succeeded"
                or run["candidateHash"] != digest(payload.model_dump())
                or run["resourceId"] != payload.objectId
            ):
                raise Conflict(
                    "This built preview does not match the configuration. Preview it again before saving."
                )
            current = await repo.get(
                "semantic_objects", {**query, "objectId": payload.objectId}
            )
            if run.get("committedVersion"):
                if current and current["version"] == run["committedVersion"]:
                    return current
                raise Conflict(
                    "This planning level changed after the previous Save. Reload metadata."
                )
            self.check_version(rev, run["revisionVersion"])
            if (current["version"] if current else None) != run["expectedVersion"]:
                raise Conflict(
                    "The saved planning level changed. Reload before saving."
                )
            objects = await self.objects(repo, model, revision)
            changed = (
                not current
                or digest({k: current[k] for k in payload.model_dump()})
                != run["candidateHash"]
            )
            document = {
                **payload.model_dump(),
                **query,
                "version": (current["version"] if current else 0) + int(changed),
                "createdAt": current["createdAt"] if current else now(),
                "updatedAt": now() if changed else current["updatedAt"],
            }
            proposed = [o for o in objects if o["objectId"] != document["objectId"]] + [
                document
            ]
            if len(proposed) > self.max_objects:
                raise Conflict("The revision has reached its metadata object limit.")
            self.validate(
                await self.effective(repo, model, revision, proposed),
                rev["basePlanningLevelId"],
            )
            inputs = await self.capture(repo, model, revision, document, proposed)
            if inputs["inputHash"] != run["inputHash"]:
                raise Conflict(
                    "Build dependencies changed. Preview the configuration again."
                )
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": run["artifactId"]},
                "Built preview",
            )
            if artifact["state"] != "ready":
                raise Conflict(
                    "This built preview expired. Preview the configuration again."
                )
            if changed:
                if current:
                    await repo.replace(
                        "semantic_objects",
                        {
                            **query,
                            "objectId": payload.objectId,
                            "version": current["version"],
                        },
                        document,
                    )
                else:
                    await repo.insert("semantic_objects", document)
            identity = {
                **query,
                "resourceKind": "planningLevel",
                "resourceId": payload.objectId,
            }
            binding = {
                **identity,
                "artifactId": artifact["artifactId"],
                "definitionHash": run["definitionHash"],
                "inputHash": run["inputHash"],
                "builtAt": artifact["builtAt"],
            }
            if await repo.get("engine_artifact_bindings", identity):
                await repo.replace("engine_artifact_bindings", identity, binding)
            else:
                await repo.insert("engine_artifact_bindings", binding)
            # Serialize Save against collection and preserve the validated candidate.
            await repo.replace(
                "engine_artifacts",
                {"artifactId": artifact["artifactId"]},
                {
                    **artifact,
                    "pinVersion": artifact.get("pinVersion", 0) + 1,
                    "committedAt": now(),
                },
            )
            await repo.replace(
                "engine_runs",
                {"runId": run_id},
                {**run, "committedVersion": document["version"], "committedAt": now()},
            )
            await self.touch_revision(repo, model, revision)
            return document

        return await self.store.transaction(commit)
