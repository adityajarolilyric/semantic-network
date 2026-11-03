from datetime import timedelta
from copy import deepcopy
from uuid import uuid4

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..engine.compiler import ClickHouseCompiler
from ..engine.contracts import digest
from ..engine.executor import ClickHouseExecutor
from ..engine.planner import ManagedTablePlanner
from ..engine.resolver import EffectiveSchemaResolver
from ..schemas.managed_tables import ManagedDefinition
from ..schemas.sources import SourceRegistration
from .base import Service, now, scope


class ManagedTableService(Service):
    def __init__(self, store, settings, warehouse, registration):
        super().__init__(store, settings.max_objects_per_revision)
        self.registration = registration
        self.planner = ManagedTablePlanner()
        self.compiler = ClickHouseCompiler(settings.clickhouse_database)
        self.executor = ClickHouseExecutor(warehouse)

    async def source(self, repo, model, revision, source):
        return await self.require(
            repo,
            "semantic_objects",
            {**scope(model, revision), "objectId": source, "kind": "source"},
            "Source",
        )

    async def list(self, model, revision, cursor, limit):
        rev = await self.require(
            self.store.repository,
            "semantic_revisions",
            scope(model, revision),
            "Revision",
        )
        documents = await self.store.repository.list(
            "semantic_managed_tables",
            scope(model, revision),
            key="sourceId",
            limit=self.max_objects,
        )
        if rev["metadataContractVersion"] == 1 and rev["approvalStatus"] == "published":
            known = {d["sourceId"] for d in documents}
            for source in await self.objects(self.store.repository, model, revision):
                if (
                    source["kind"] == "source"
                    and source["definition"]["inScope"]
                    and source["objectId"] not in known
                ):
                    documents.append(self.legacy_identity(source))
        for document in documents:
            binding = await self.store.repository.get(
                "engine_artifact_bindings",
                {**scope(model, revision), "sourceId": document["sourceId"]},
            )
            document["ready"] = bool(
                binding and binding["definitionHash"] == document["definitionHash"]
            )
        documents = sorted(
            (d for d in documents if cursor is None or d["sourceId"] > cursor),
            key=lambda d: d["sourceId"],
        )
        return {
            "items": documents[:limit],
            "nextCursor": documents[limit - 1]["sourceId"]
            if len(documents) > limit
            else None,
        }

    def legacy_identity(self, source):
        plan = self.planner.plan(
            source,
            ManagedDefinition(primaryKeyColumnIds=source["definition"]["keyColumns"]),
        )
        return {
            "sourceId": source["objectId"],
            "version": 0,
            "definition": plan.definition,
            "definitionHash": digest(plan.definition),
            "legacyAdapter": True,
        }

    async def get(self, model, revision, source):
        current = await self.store.repository.get(
            "semantic_managed_tables", {**scope(model, revision), "sourceId": source}
        )
        if current:
            return current
        rev = await self.require(
            self.store.repository,
            "semantic_revisions",
            scope(model, revision),
            "Revision",
        )
        if rev["metadataContractVersion"] == 1 and rev["approvalStatus"] == "published":
            raw = await self.source(self.store.repository, model, revision, source)
            return self.legacy_identity(raw)
        return await self.require(
            self.store.repository,
            "semantic_managed_tables",
            {**scope(model, revision), "sourceId": source},
            "Managed table",
        )

    async def enable(self, model, revision, table, key):
        replay = await self.store.repository.get(
            "engine_runs", {**scope(model, revision), "idempotencyKey": key}
        )
        if replay:
            if (
                replay.get("operationName") != "enable"
                or replay.get("tableName") != table
            ):
                raise Conflict(
                    "This idempotency key was already used for another request."
                )
            return self.public_run(replay)
        # Raw registration is deliberately separate: a failed first build leaves it unmanaged.
        source_id = (
            "source_" + __import__("hashlib").sha256(table.encode()).hexdigest()[:24]
        )
        objects = await self.objects(self.store.repository, model, revision)
        source = next(
            (
                o
                for o in objects
                if o["kind"] == "source" and o["definition"]["tableName"] == table
            ),
            None,
        )
        if source is None:
            try:
                source = await self.registration.register(
                    model,
                    revision,
                    SourceRegistration(
                        objectId=source_id, tableName=table, inScope=False
                    ),
                )
            except Conflict:
                source = await self.source(
                    self.store.repository, model, revision, source_id
                )
        query = {**scope(model, revision), "sourceId": source["objectId"]}
        current = await self.store.repository.get("semantic_managed_tables", query)
        definition = (
            self.input_definition(current["definition"])
            if current
            else ManagedDefinition(
                primaryKeyColumnIds=source["definition"]["keyColumns"]
            )
        )
        return await self.submit(
            model,
            revision,
            source["objectId"],
            definition,
            current["version"] if current else 0,
            key,
            operation_name="enable",
        )

    @staticmethod
    def input_definition(definition):
        return ManagedDefinition.model_validate(
            {k: v for k, v in definition.items() if k != "columns"}
        )

    async def submit(
        self,
        model,
        revision,
        source_id,
        payload,
        expected,
        key,
        *,
        repair=False,
        operation_name="save",
    ):
        if not key or len(key) > 128:
            raise MetadataError(
                "invalid_idempotency_key",
                "Supply an Idempotency-Key of 1 to 128 characters.",
                400,
            )
        request_hash = digest([source_id, payload.model_dump(), expected, repair])
        previous = await self.store.repository.get(
            "engine_runs", {**scope(model, revision), "idempotencyKey": key}
        )
        if previous:
            if previous["requestHash"] != request_hash:
                raise Conflict(
                    "This idempotency key was already used for another request."
                )
            return self.public_run(previous)

        async def operation(repo):
            query = scope(model, revision)
            previous = await repo.get("engine_runs", {**query, "idempotencyKey": key})
            if previous:
                if previous["requestHash"] != request_hash:
                    raise Conflict(
                        "This idempotency key was already used for another request."
                    )
                return self.public_run(previous)
            rev = await self.require(repo, "semantic_revisions", query, "Revision")
            if rev["approvalStatus"] != "draft" and not repair:
                raise ImmutableRevision()
            source = await self.source(repo, model, revision, source_id)
            current = await repo.get(
                "semantic_managed_tables", {**query, "sourceId": source_id}
            )
            if (current["version"] if current else 0) != expected:
                raise Conflict()
            if repair:
                frozen = (
                    current["definition"]
                    if current
                    else self.legacy_identity(source)["definition"]
                )
                if (
                    not current and rev["metadataContractVersion"] != 1
                ) or payload.model_dump() != self.input_definition(frozen).model_dump():
                    raise Conflict(
                        "Repair may only rebuild the saved managed definition."
                    )
            plan = self.planner.plan(source, payload)
            objects = await self.objects(repo, model, revision)
            managed = await repo.list(
                "semantic_managed_tables", query, key="sourceId", limit=self.max_objects
            )
            candidate = {"sourceId": source_id, "definition": plan.definition}
            effective = EffectiveSchemaResolver.apply(
                objects,
                [d for d in managed if d["sourceId"] != source_id] + [candidate],
            )
            for obj in effective:
                if obj["objectId"] == source_id:
                    obj["definition"]["inScope"] = True
            self.validate(effective, rev["basePlanningLevelId"])
            run_id, artifact_id = "run_" + uuid4().hex, "artifact_" + uuid4().hex
            run = {
                **query,
                "runId": run_id,
                "resourceKind": "managedTable",
                "resourceId": source_id,
                "sourceId": source_id,
                "idempotencyKey": key,
                "requestHash": request_hash,
                "operationName": operation_name,
                "tableName": source["definition"]["tableName"],
                "expectedVersion": expected,
                "revisionVersion": rev["version"],
                "definition": plan.definition,
                "definitionHash": digest(plan.definition),
                "repair": repair,
                "artifactId": artifact_id,
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
            await repo.insert(
                "engine_artifacts",
                {
                    **query,
                    "sourceId": source_id,
                    "artifactId": artifact_id,
                    "runId": run_id,
                    "physicalName": "__semantic_view_" + artifact_id[9:],
                    "definitionHash": run["definitionHash"],
                    "columns": plan.definition["columns"],
                    "state": "candidate",
                    "createdAt": now(),
                },
            )
            return self.public_run(run)

        try:
            return await self.store.transaction(operation)
        except Conflict:
            # A competing API process may have committed this same request/key.
            previous = await self.store.repository.get(
                "engine_runs", {**scope(model, revision), "idempotencyKey": key}
            )
            if previous and previous["requestHash"] == request_hash:
                return self.public_run(previous)
            raise

    @staticmethod
    def public_run(run):
        result = {
            k: run[k] for k in ("runId", "state", "createdAt", "updatedAt", "error")
        }
        result.update(
            resourceKind=run.get("resourceKind", "managedTable"),
            resourceId=run.get("resourceId", run.get("sourceId")),
        )
        if "sourceId" in run:
            result["sourceId"] = run["sourceId"]
        return result

    async def run_status(self, model, revision, run_id):
        run = await self.require(
            self.store.repository,
            "engine_runs",
            {**scope(model, revision), "runId": run_id},
            "Engine run",
        )
        return self.public_run(run)

    async def cancel(self, model, revision, run_id):
        async def operation(repo):
            query = {**scope(model, revision), "runId": run_id}
            run = await self.require(repo, "engine_runs", query, "Engine run")
            if run["state"] in ("queued", "running"):
                run = {
                    **run,
                    "state": "cancelled",
                    "fence": run["fence"] + 1,
                    "updatedAt": now(),
                }
                await repo.replace("engine_runs", query, run)
            return self.public_run(run)

        return await self.store.transaction(operation)

    async def disable(self, model, revision, source_id, expected):
        async def operation(repo):
            rev = await self.touch_revision(repo, model, revision)
            source = await self.source(repo, model, revision, source_id)
            managed = await self.require(
                repo,
                "semantic_managed_tables",
                {**scope(model, revision), "sourceId": source_id},
                "Managed table",
            )
            self.check_version(managed, expected)
            updated = deepcopy(source)
            updated["definition"]["inScope"] = False
            updated.update(version=source["version"] + 1, updatedAt=now())
            objects = await self.objects(repo, model, revision)
            effective = await EffectiveSchemaResolver.load(
                repo,
                model,
                revision,
                [updated if o["objectId"] == source_id else o for o in objects],
            )
            self.validate(effective, rev["basePlanningLevelId"])
            await repo.replace(
                "semantic_objects",
                {
                    **scope(model, revision),
                    "objectId": source_id,
                    "version": source["version"],
                },
                updated,
            )
            await repo.replace(
                "semantic_managed_tables",
                {**scope(model, revision), "sourceId": source_id},
                {**managed, "version": expected + 1, "updatedAt": now()},
            )

        await self.store.transaction(operation)

    async def schema(self, model, revision, source_id, payload):
        source = await self.source(self.store.repository, model, revision, source_id)
        logical = self.planner.plan(source, payload)
        return {"columns": list(logical.schema.columns)}

    async def preview(self, model, revision, source_id, payload=None):
        source = await self.source(self.store.repository, model, revision, source_id)
        table = None
        if payload is None:
            current = await self.get(model, revision, source_id)
            payload = self.input_definition(current["definition"])
            binding = await self.require(
                self.store.repository,
                "engine_artifact_bindings",
                {**scope(model, revision), "sourceId": source_id},
                "Active managed view; build this table first",
            )
            if binding["definitionHash"] != current["definitionHash"]:
                raise Conflict("The managed view is not ready for this definition.")
            artifact = await self.require(
                self.store.repository,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
                "Artifact",
            )
            table = artifact["physicalName"]
        await self.executor.inspect(source)
        logical = self.planner.plan(source, payload)
        compiled = self.compiler.compile(logical)
        if table:
            await self.executor.verify(artifact, compiled)
        return await self.executor.preview(compiled, table)

    async def output_attribute_values(
        self, model, revision, source_id, column_id, offset, limit
    ):
        """Discover through the saved managed view; keep its generation alive while reading."""
        read_id = "read_" + uuid4().hex

        async def capture(repo):
            source = await self.source(repo, model, revision, source_id)
            current = await self.require(
                repo,
                "semantic_managed_tables",
                {**scope(model, revision), "sourceId": source_id},
                "Managed definition",
            )
            binding = await self.require(
                repo,
                "engine_artifact_bindings",
                {**scope(model, revision), "sourceId": source_id},
                "Saved managed view; save this table first",
            )
            if binding["definitionHash"] != current["definitionHash"]:
                raise Conflict(
                    "Save and build the current managed view before discovering values."
                )
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
                "Managed view",
            )
            if artifact["state"] != "ready":
                raise Conflict("The managed view is no longer ready. Retry discovery.")
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
                    "expiresAt": now()
                    + timedelta(
                        seconds=max(
                            180,
                            4 * self.executor.warehouse.settings.clickhouse_timeout_sec
                            + 60,
                        )
                    ),
                },
            )
            return source, current, artifact

        source, current, artifact = await self.store.transaction(capture)
        identity = {"readId": read_id, "artifactId": artifact["artifactId"]}
        try:
            await self.executor.inspect(source)
            compiled = self.compiler.compile(
                self.planner.plan(source, self.input_definition(current["definition"]))
            )
            await self.executor.verify(artifact, compiled)
            result = await self.executor.attribute_values(
                {
                    "definition": {
                        "tableName": artifact["physicalName"],
                        "columns": list(compiled.schema.columns),
                    }
                },
                column_id,
                offset,
                limit,
            )

            async def verify(repo):
                lease = await repo.get("engine_read_leases", identity)
                binding = await repo.get(
                    "engine_artifact_bindings",
                    {**scope(model, revision), "sourceId": source_id},
                )
                if (
                    not lease
                    or lease["expiresAt"] <= now()
                    or not binding
                    or binding["artifactId"] != artifact["artifactId"]
                ):
                    raise Conflict("The managed view changed during discovery. Retry.")

            await self.store.transaction(verify)
            return {**result, "artifactId": artifact["artifactId"]}
        finally:
            await self.store.repository.delete("engine_read_leases", identity)

    async def preflight(self, model, revision):
        """Check outside Mongo transaction, then publication must compare revision version."""
        repo = self.store.repository
        rev = await self.require(
            repo, "semantic_revisions", scope(model, revision), "Revision"
        )
        if rev["metadataContractVersion"] < 2:
            return rev["version"]
        for source in await self.objects(repo, model, revision):
            if source["kind"] != "source" or not source["definition"]["inScope"]:
                continue
            current = await self.get(model, revision, source["objectId"])
            binding = await self.require(
                repo,
                "engine_artifact_bindings",
                {**scope(model, revision), "sourceId": source["objectId"]},
                "Ready managed view",
            )
            if binding["definitionHash"] != current["definitionHash"]:
                raise Conflict("Build all managed views before publication.")
            await self.executor.inspect(source)
            compiled = self.compiler.compile(
                self.planner.plan(source, self.input_definition(current["definition"]))
            )
            artifact = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": binding["artifactId"]},
                "Managed artifact",
            )
            await self.executor.verify(artifact, compiled)
            await self.executor.checks(compiled)
        return rev["version"]

    async def upgrade_contract(self, model, revision, expected):
        async def operation(repo):
            rev = await self.require(
                repo, "semantic_revisions", scope(model, revision), "Revision"
            )
            self.check_version(rev, expected)
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            for source in await self.objects(repo, model, revision):
                if source["kind"] == "source" and source["definition"]["inScope"]:
                    current = await self.require(
                        repo,
                        "semantic_managed_tables",
                        {**scope(model, revision), "sourceId": source["objectId"]},
                        "Initialized managed definition",
                    )
                    binding = await self.require(
                        repo,
                        "engine_artifact_bindings",
                        {**scope(model, revision), "sourceId": source["objectId"]},
                        "Ready managed view",
                    )
                    if current["definitionHash"] != binding["definitionHash"]:
                        raise Conflict(
                            "Initialize every enabled managed table before upgrading."
                        )
            updated = {
                **rev,
                "metadataContractVersion": 2,
                "version": expected + 1,
                "updatedAt": now(),
            }
            await repo.replace(
                "semantic_revisions",
                {**scope(model, revision), "version": expected},
                updated,
            )
            return updated

        return await self.store.transaction(operation)
