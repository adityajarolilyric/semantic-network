from datetime import timedelta
from uuid import uuid4

from ..engine.object_views.inputs import ObjectViewInputs
from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..engine.sql.contracts import Check, ExecutionPlan, digest
from ..engine.execution.builds.populations import DimensionBuildHandler
from ..engine.populations.compiler import DimensionCompiler
from ..warehouse.dimension_execution import DimensionTableExecutor
from ..engine.populations.planner import DimensionPlanner
from ..engine.execution.inputs.managed import ManagedInputCapture
from ..engine.execution.inputs.session import PopulationCaptureSession
from ..warehouse.current_storage import STORAGE_POLICY, TableStorage
from ..engine.populations.virtual import is_virtual_dimension, virtual_population
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ObjectLookup,
    ReadLeaseLookup,
    RevisionLookup,
    RunLookup,
)
from .base import Service, now, scope


class DimensionBuildService(Service):
    def __init__(self, store, settings, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.managed = settings, managed
        self.input_capture = ManagedInputCapture()
        self.object_inputs = ObjectViewInputs()
        self.planner = DimensionPlanner(settings.resolution_max_rows)
        self.compiler = DimensionCompiler(settings.clickhouse_database)
        self.storage = TableStorage(store, managed.executor.warehouse)
        self.executor = DimensionTableExecutor(managed.executor.warehouse, settings)
        self.executor.storage = self.storage

        self.handler = DimensionBuildHandler(self)

    @staticmethod
    def definition_hash(level):
        return digest({"name": level["name"], "definition": level["definition"]})

    async def capture_population_inputs(
        self,
        repo,
        model,
        revision,
        level,
        objects,
        seen=None,
        *,
        virtual=False,
        session=None,
    ):
        session = session or PopulationCaptureSession(
            {o["objectId"]: o for o in objects}
        )
        by_id = session.objects_by_id
        seen = set() if seen is None else seen
        if level["objectId"] in seen:
            raise Conflict("Planning dependencies contain a cycle.")
        cache_key = (level["objectId"], virtual)
        if cache_key in session.populations:
            return session.populations[cache_key]
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
            parent = by_id[population["sourceDimensionId"]]
            parent_capture = await self.capture_population_inputs(
                repo,
                model,
                revision,
                parent,
                objects,
                seen,
                virtual=virtual,
                session=session,
            )
            if virtual or is_virtual_dimension(parent):
                plan = self.planner.plan(parent, objects, parent_capture["relations"])
                compiled = self.compiler.compile(plan)
                relations.update(parent_capture["relations"])
                versions.update(parent_capture["versions"])
                bindings.update(parent_capture["bindings"])
                managed_inputs.update(parent_capture["managed"])
                relations[parent["objectId"]] = {
                    "physicalName": "__dimension_preview",
                    "artifactId": "virtual_" + parent_capture["inputHash"],
                    "columns": list(plan.schema.columns),
                    "query": compiled.sql,
                }
                versions[parent["objectId"]] = parent["version"]
            else:
                binding = await metadata_repositories(repo).bindings.require(
                    BindingLookup(
                        model_id=model,
                        revision_id=revision,
                        resource_kind="dimension",
                        resource_id=parent["objectId"],
                    ),
                    "Ready parent planning table; build the parent first",
                )
                if (
                    binding["definitionHash"] != self.definition_hash(parent)
                    or binding["inputHash"] != parent_capture["inputHash"]
                ):
                    raise Conflict("The parent planning table needs rebuilding first.")
                artifact = await metadata_repositories(repo).artifacts.require(
                    ArtifactLookup(artifact_id=binding["artifactId"]),
                    "Parent table artifact",
                )
                if artifact["state"] != "ready":
                    raise Conflict("The parent table is not ready.")
                relations[parent["objectId"]] = artifact
                versions[parent["objectId"]] = parent["version"]
                bindings[parent["objectId"]] = binding
        dimension_ids = {b["memberCatalogId"] for b in definition["memberBindings"]}
        calendar = (population.get("inputTransform") or {}).get("calendarResolution")
        if calendar:
            dimension_ids.add(calendar["memberCatalogId"])
        for did in dimension_ids:
            dim = by_id[did]
            versions[dim["objectId"]] = dim["version"]
            source_ids.add(dim["definition"]["sourceId"])
        for sid in sorted(source_ids):
            if sid not in session.sources:
                session.sources[sid] = await self.input_capture.capture_source(
                    repo, model, revision, by_id[sid]
                )
            source_snapshot = session.sources[sid]
            relations[sid] = source_snapshot.artifact
            versions[sid] = source_snapshot.source_version
            bindings[sid] = source_snapshot.binding
            managed_inputs[sid] = source_snapshot.definition
        if session.object_views is None:
            session.object_views = await self.object_inputs.load(
                repo, model, revision, objects
            )
        saved_views, saved_bindings = self.object_inputs.select(
            level, objects, *session.object_views
        )
        # A draft candidate's own hash differs, so it cannot accidentally read its
        # previously saved view while compiling a changed definition.
        relations.update(saved_views)
        bindings.update(saved_bindings)
        captured = {
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
        session.populations[cache_key] = captured
        return captured

    async def submit(
        self,
        model,
        revision,
        level_id,
        expected,
        key,
        payload=None,
        *,
        query_memory_bytes=None,
    ):
        if not key or len(key) > 128:
            raise MetadataError(
                "invalid_idempotency_key",
                "Supply an Idempotency-Key of 1 to 128 characters.",
                400,
            )
        request_hash = digest(
            ["dimension", level_id, expected]
            + ([payload.model_dump()] if payload else [])
        )

        async def operation(repo):
            query = scope(model, revision)
            previous = await metadata_repositories(repo).runs.find(
                RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
            )
            if previous:
                if previous["requestHash"] != request_hash:
                    raise Conflict("This idempotency key belongs to another request.")
                return self.managed.public_run(previous)
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            objects = await self.graph_objects(repo, model, revision)
            if payload is not None:
                current = await metadata_repositories(repo).objects.find(
                    ObjectLookup(
                        model_id=model, revision_id=revision, object_id=level_id
                    )
                )
                if current and (
                    current["kind"] != "dimension" or current["version"] != expected
                ):
                    raise Conflict(
                        "This planning definition changed. Reload before previewing."
                    )
                if not current and expected is not None:
                    raise Conflict(
                        "This dimension was removed. Reload before previewing."
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
                level = await metadata_repositories(repo).objects.require(
                    ObjectLookup(
                        model_id=model,
                        revision_id=revision,
                        object_id=level_id,
                        kind="dimension",
                    ),
                    "Dimension",
                )
                self.check_version(level, expected)
            self.validate(
                await self.effective(repo, model, revision, objects),
                rev["baseDimensionId"],
            )
            captured = await self.capture_population_inputs(
                repo, model, revision, level, objects
            )
            self.planner.plan(level, objects, captured["relations"])
            run_id, artifact_id = "run_" + uuid4().hex, "artifact_" + uuid4().hex
            run = {
                **query,
                "runId": run_id,
                "resourceKind": "dimensionPreview" if payload else "dimension",
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
                "queryMemoryBytes": query_memory_bytes
                or self.settings.planning_max_bytes,
                "state": "queued",
                "fence": 0,
                "leaseUntil": now(),
                "createdAt": now(),
                "updatedAt": now(),
                "error": None,
            }
            await metadata_repositories(repo).runs.insert(run)
            for obj in objects:
                await metadata_repositories(repo).inputs.insert(
                    {"runId": run_id, "inputId": obj["objectId"], "document": obj}
                )
            for sid, relation in captured["relations"].items():
                await metadata_repositories(repo).inputs.insert(
                    {
                        "runId": run_id,
                        "inputId": "__relation_" + sid,
                        "document": {
                            "resourceId": sid,
                            "relation": relation,
                            "managed": captured["managed"].get(sid),
                        },
                    }
                )
            await metadata_repositories(repo).artifacts.insert(
                await self.storage.reserve(
                    repo,
                    {
                        **query,
                        "resourceKind": "dimension",
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
                )
            )
            pinned_relations = {
                relation["artifactId"]: relation
                for relation in captured["relations"].values()
            }
            for relation in pinned_relations.values():
                if relation.get("query"):
                    continue
                artifact = await metadata_repositories(repo).artifacts.require(
                    ArtifactLookup(artifact_id=relation["artifactId"]), "Input artifact"
                )
                if artifact["state"] != "ready":
                    raise Conflict("An input artifact was retired. Retry the build.")
                # Shared write serializes retirement against acquisition of a read pin.
                await metadata_repositories(repo).artifacts.replace(
                    ArtifactLookup(artifact_id=artifact["artifactId"]),
                    {**artifact, "pinVersion": artifact.get("pinVersion", 0) + 1},
                )
                await metadata_repositories(repo).pins.insert(
                    {"runId": run_id, "artifactId": artifact["artifactId"]}
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

    async def status(self, model, revision, level_id, *, preview=False):
        repo = self.store.repository
        level = await metadata_repositories(repo).objects.require(
            ObjectLookup(
                model_id=model,
                revision_id=revision,
                object_id=level_id,
                kind="dimension",
            ),
            "Dimension",
        )
        if is_virtual_dimension(level):
            objects = await self.graph_objects(repo, model, revision)
            captured = await self.capture_population_inputs(
                repo, model, revision, level, objects, virtual=True
            )
            compiled = self.compiler.compile(
                self.planner.plan(level, objects, captured["relations"])
            )
            artifact = virtual_population(level, compiled, captured["inputHash"])
            result = {
                "state": "ready",
                "storage": "virtual",
                "columns": artifact["columns"],
                "rows": [],
                "rowCount": None,
                "builtAt": None,
            }
            if preview:
                await self.executor.checks(
                    ExecutionPlan(
                        None,
                        tuple(Check(c["code"], c["sql"]) for c in artifact["checks"]),
                        None,
                    )
                )
                result.update(await self.executor.preview(compiled))
            return result
        binding = await metadata_repositories(repo).bindings.find(
            BindingLookup(
                model_id=model,
                revision_id=revision,
                resource_kind="dimension",
                resource_id=level_id,
            )
        )
        result = {
            "state": "not_built",
            "rowCount": None,
            "columns": [],
            "rows": [],
            "builtAt": None,
        }
        runs = await metadata_repositories(repo).runs.list(
            RunLookup(
                model_id=model,
                revision_id=revision,
                resource_kind="dimension",
                resource_id=level_id,
            ),
            order_by="createdAt",
            limit=1,
            newest_first=True,
        )
        active = None
        for state in ("running", "queued"):
            pending = await metadata_repositories(repo).runs.list(
                RunLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="dimension",
                    resource_id=level_id,
                    state=state,
                ),
                order_by="runId",
                limit=1,
            )
            if pending:
                active = pending[0]
                break
        if binding:
            artifact = await metadata_repositories(repo).artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]),
                "Planning table artifact",
            )
            result.update(
                state="ready",
                rowCount=artifact["rowCount"],
                columns=artifact["columns"],
                builtAt=binding["builtAt"],
            )
            objects = await self.graph_objects(repo, model, revision)
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            if rev["approvalStatus"] == "draft":
                try:
                    current = await self.capture_population_inputs(
                        repo, model, revision, level, objects
                    )
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

                result["rows"] = await warehouse.queries.preview_population(
                    artifact, self.executor.execution_settings()
                )
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
            await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            objects = await self.graph_objects(repo, model, revision)
            candidate = {**payload.model_dump(), "version": 0}
            objects = [o for o in objects if o["objectId"] != candidate["objectId"]] + [
                candidate
            ]
            captured = await self.capture_population_inputs(
                repo, model, revision, candidate, objects, virtual=True
            )
            return candidate, objects, captured

        candidate, objects, captured = await self.store.transaction(snapshot)
        plan = self.planner.plan(candidate, objects, captured["relations"])
        return {"columns": list(plan.schema.columns)}

    async def preview(self, model, revision, payload, *, sql_only=False):
        async def snapshot(repo):
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            objects = await self.graph_objects(repo, model, revision)
            candidate = {**payload.model_dump(), "version": 0}
            candidates = [
                o for o in objects if o["objectId"] != candidate["objectId"]
            ] + [candidate]
            self.validate(
                await self.effective(repo, model, revision, candidates),
                rev["baseDimensionId"],
            )
            capture = await self.capture_population_inputs(
                repo, model, revision, candidate, candidates, virtual=True
            )
            return candidate, candidates, capture

        level, objects, captured = await self.store.transaction(snapshot)
        plan = self.planner.plan(level, objects, captured["relations"])
        compiled = self.compiler.compile(plan)
        if sql_only:
            return {"sql": compiled.formatted_sql, "dialect": "clickhouse"}
        await self.storage.ensure_inputs(
            r for r in captured["relations"].values() if not r.get("query")
        )
        if level["definition"]["population"].get("inputTransform"):
            await self.executor.checks(compiled)
        estimate = await self.executor.estimate(plan, self.compiler)
        return {**await self.executor.preview(compiled), **estimate}

    async def preflight(self, model, revision):
        for level in await self.graph_objects(self.store.repository, model, revision):
            if level["kind"] == "dimension":
                status = await self.status(
                    model, revision, level["objectId"], preview=True
                )
                if status["state"] != "ready":
                    raise Conflict(
                        "Build all dimension tables with current inputs before publishing."
                    )

    async def draft_preview(self, model, revision, run_id):
        read_id = "read_" + uuid4().hex

        async def snapshot(repo):
            run = await metadata_repositories(repo).runs.require(
                RunLookup(model_id=model, revision_id=revision, run_id=run_id),
                "Preview build",
            )
            if not run.get("previewOnly") or run["state"] != "succeeded":
                raise Conflict("The preview build is not ready.")
            artifact = await metadata_repositories(repo).artifacts.require(
                ArtifactLookup(artifact_id=run["artifactId"]), "Preview generation"
            )
            if artifact["state"] != "ready":
                raise Conflict(
                    "This preview has expired. Preview the configuration again."
                )
            await metadata_repositories(repo).artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {**artifact, "pinVersion": artifact.get("pinVersion", 0) + 1},
            )
            await metadata_repositories(repo).read_leases.insert(
                {
                    "readId": read_id,
                    "artifactId": artifact["artifactId"],
                    "expiresAt": now() + timedelta(seconds=180),
                }
            )
            return artifact

        artifact = await self.store.transaction(snapshot)
        try:
            await self.storage.ensure_inputs([artifact])
            warehouse = self.executor.warehouse

            return {
                "state": "preview",
                "rowCount": artifact["rowCount"],
                "columns": artifact["columns"],
                "rows": await warehouse.queries.preview_draft_population(
                    artifact, self.executor.execution_settings()
                ),
            }
        finally:
            await self.store.transaction(
                lambda repo: metadata_repositories(repo).read_leases.delete(
                    ReadLeaseLookup(read_id=read_id, artifact_id=artifact["artifactId"])
                )
            )

    async def commit_draft(self, model, revision, run_id, payload):
        query = scope(model, revision)

        async def commit(repo, validate_only=False):
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            run = await metadata_repositories(repo).runs.require(
                RunLookup(model_id=model, revision_id=revision, run_id=run_id),
                "Preview build",
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
            current = await metadata_repositories(repo).objects.find(
                ObjectLookup(
                    model_id=model, revision_id=revision, object_id=payload.objectId
                )
            )
            if run.get("committedVersion"):
                if current and current["version"] == run["committedVersion"]:
                    return current
                raise Conflict(
                    "This dimension changed after the previous Save. Reload metadata."
                )
            self.check_version(rev, run["revisionVersion"])
            if (current["version"] if current else None) != run["expectedVersion"]:
                raise Conflict("The saved dimension changed. Reload before saving.")
            objects = await self.graph_objects(repo, model, revision)
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
                rev["baseDimensionId"],
            )
            inputs = await self.capture_population_inputs(
                repo, model, revision, document, proposed
            )
            if inputs["inputHash"] != run["inputHash"]:
                raise Conflict(
                    "Build dependencies changed. Preview the configuration again."
                )
            artifact = await metadata_repositories(repo).artifacts.require(
                ArtifactLookup(artifact_id=run["artifactId"]), "Built preview"
            )
            if artifact["state"] != "ready":
                raise Conflict(
                    "This built preview expired. Preview the configuration again."
                )
            if validate_only:
                if artifact.get("storageMode") == "current":
                    await metadata_repositories(repo).runs.replace(
                        RunLookup(run_id=run_id), {**run, "publicationPending": True}
                    )
                return artifact
            if changed:
                if current:
                    await metadata_repositories(repo).objects.replace(
                        ObjectLookup(
                            model_id=model,
                            revision_id=revision,
                            object_id=payload.objectId,
                            version=current["version"],
                        ),
                        document,
                    )
                else:
                    await metadata_repositories(repo).objects.insert(document)
            identity = {
                **query,
                "resourceKind": "dimension",
                "resourceId": payload.objectId,
            }
            binding = {
                **identity,
                "artifactId": artifact["artifactId"],
                "definitionHash": run["definitionHash"],
                "inputHash": run["inputHash"],
                "builtAt": artifact["builtAt"],
            }
            if await metadata_repositories(repo).bindings.find(
                BindingLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="dimension",
                    resource_id=payload.objectId,
                )
            ):
                await metadata_repositories(repo).bindings.replace(
                    BindingLookup(
                        model_id=model,
                        revision_id=revision,
                        resource_kind="dimension",
                        resource_id=payload.objectId,
                    ),
                    binding,
                )
            else:
                await metadata_repositories(repo).bindings.insert(binding)
            # Serialize Save against collection and preserve the validated candidate.
            await metadata_repositories(repo).artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {
                    **artifact,
                    "pinVersion": artifact.get("pinVersion", 0) + 1,
                    "committedAt": now(),
                },
            )
            await metadata_repositories(repo).runs.replace(
                RunLookup(run_id=run_id),
                {
                    **run,
                    "committedVersion": document["version"],
                    "committedAt": now(),
                    "publicationPending": False,
                },
            )
            await self.touch_revision(repo, model, revision)
            return document

        artifact = await self.store.transaction(
            lambda repo: commit(repo, validate_only=True)
        )
        if artifact.get("storageMode") != "current" or "_build_" not in artifact.get(
            "physicalName", ""
        ):
            return await self.store.transaction(commit)
        artifact["previewCommit"] = True
        result = None

        async def save():
            nonlocal result
            result = await self.store.transaction(commit)

        try:
            await self.storage.activate([artifact], save)
        except BaseException:

            async def release_failed_preview(repo):
                repositories = metadata_repositories(repo)
                current = await repositories.artifacts.require(
                    ArtifactLookup(artifact_id=artifact["artifactId"]), "Preview"
                )
                if current["state"] == "dropped":
                    run = await repositories.runs.require(
                        RunLookup(run_id=run_id), "Preview build"
                    )
                    await repositories.runs.replace(
                        RunLookup(run_id=run_id), {**run, "publicationPending": False}
                    )

            await self.store.transaction(release_failed_preview)
            raise
        return result
