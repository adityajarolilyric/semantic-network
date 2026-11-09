from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..engine.transformations.pivot.compiler import ClickHouseCompiler
from ..engine.sql.contracts import digest
from ..warehouse.managed_execution import ClickHouseExecutor
from ..engine.execution.inputs.managed import ManagedInputVerifier
from ..engine.transformations.pivot.planner import ManagedTablePlanner
from ..engine.execution.inputs.schema import EffectiveSchemaResolver
from ..engine.execution.storage.view_names import (
    VIEW_NAMING_POLICY,
    source_view_name,
    source_view_role,
)
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ManagedTableLookup,
    ObjectLookup,
    ReadLeaseLookup,
    RevisionLookup,
    RunLookup,
)
from ..schemas.managed_tables import ManagedDefinition
from ..schemas.sources import SourceRegistration
from .base import Service, now, scope


class ManagedTableService(Service):
    def __init__(self, store, settings, warehouse, registration):
        super().__init__(store, settings.max_objects_per_revision)
        self.registration = registration
        self.planner = ManagedTablePlanner
        self.compiler = ClickHouseCompiler(settings.clickhouse_database)
        self.executor = ClickHouseExecutor(warehouse)
        self.input_verifier = ManagedInputVerifier(self)

    async def source(self, repo, model, revision, source):
        return await metadata_repositories(repo).objects.require(
            ObjectLookup(
                model_id=model, revision_id=revision, object_id=source, kind="source"
            ),
            "Source",
        )

    async def list(self, model, revision, cursor, limit):
        rev = await metadata_repositories(self.store.repository).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        documents = await metadata_repositories(
            self.store.repository
        ).managed_tables.list(
            ManagedTableLookup(model_id=model, revision_id=revision),
            order_by="sourceId",
            limit=self.max_objects,
        )
        if rev["metadataContractVersion"] == 1 and rev["approvalStatus"] == "published":
            known = {d["sourceId"] for d in documents}
            for source in await self.graph_objects(
                self.store.repository, model, revision
            ):
                if (
                    source["kind"] == "source"
                    and source["definition"]["inScope"]
                    and source["objectId"] not in known
                ):
                    documents.append(self.legacy_identity(source))
        for document in documents:
            binding = await metadata_repositories(self.store.repository).bindings.find(
                BindingLookup(
                    model_id=model, revision_id=revision, source_id=document["sourceId"]
                )
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
        current = await metadata_repositories(
            self.store.repository
        ).managed_tables.find(
            ManagedTableLookup(model_id=model, revision_id=revision, source_id=source)
        )
        if current:
            return current
        rev = await metadata_repositories(self.store.repository).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        if rev["metadataContractVersion"] == 1 and rev["approvalStatus"] == "published":
            raw = await self.source(self.store.repository, model, revision, source)
            return self.legacy_identity(raw)
        return await metadata_repositories(
            self.store.repository
        ).managed_tables.require(
            ManagedTableLookup(model_id=model, revision_id=revision, source_id=source),
            "Managed table",
        )

    async def enable(self, model, revision, table, key):
        replay = await metadata_repositories(self.store.repository).runs.find(
            RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
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
        source_id = "source_" + sha256(table.encode()).hexdigest()[:24]
        objects = await self.graph_objects(self.store.repository, model, revision)
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
        current = await metadata_repositories(
            self.store.repository
        ).managed_tables.find(
            ManagedTableLookup(
                model_id=model, revision_id=revision, source_id=source["objectId"]
            )
        )
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
        previous = await metadata_repositories(self.store.repository).runs.find(
            RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
        )
        if previous:
            if previous["requestHash"] != request_hash:
                raise Conflict(
                    "This idempotency key was already used for another request."
                )
            return self.public_run(previous)

        async def operation(repo):
            query = scope(model, revision)
            previous = await metadata_repositories(repo).runs.find(
                RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
            )
            if previous:
                if previous["requestHash"] != request_hash:
                    raise Conflict(
                        "This idempotency key was already used for another request."
                    )
                return self.public_run(previous)
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            if rev["approvalStatus"] != "draft" and not repair:
                raise ImmutableRevision()
            source = await self.source(repo, model, revision, source_id)
            current = await metadata_repositories(repo).managed_tables.find(
                ManagedTableLookup(
                    model_id=model, revision_id=revision, source_id=source_id
                )
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
            objects = await self.graph_objects(repo, model, revision)
            managed = await metadata_repositories(repo).managed_tables.list(
                ManagedTableLookup(model_id=model, revision_id=revision),
                order_by="sourceId",
                limit=self.max_objects,
            )
            candidate = {"sourceId": source_id, "definition": plan.definition}
            effective = EffectiveSchemaResolver.apply(
                objects,
                [d for d in managed if d["sourceId"] != source_id] + [candidate],
            )
            for obj in effective:
                if obj["objectId"] == source_id:
                    obj["definition"]["inScope"] = True
            self.validate(effective, rev["baseDimensionId"])
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
            await metadata_repositories(repo).runs.insert(run)
            for obj in objects:
                await metadata_repositories(repo).inputs.insert(
                    {"runId": run_id, "inputId": obj["objectId"], "document": obj}
                )
            await metadata_repositories(repo).artifacts.insert(
                {
                    **query,
                    "sourceId": source_id,
                    "artifactId": artifact_id,
                    "runId": run_id,
                    "physicalName": source_view_name(
                        source, artifact_id, source_view_role(source, objects)
                    ),
                    "viewNamingPolicy": VIEW_NAMING_POLICY,
                    "definitionHash": run["definitionHash"],
                    "columns": plan.definition["columns"],
                    "state": "candidate",
                    "createdAt": now(),
                }
            )
            return self.public_run(run)

        try:
            return await self.store.transaction(operation)
        except Conflict:
            # A competing API process may have committed this same request/key.
            previous = await metadata_repositories(self.store.repository).runs.find(
                RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
            )
            if previous and previous["requestHash"] == request_hash:
                return self.public_run(previous)
            raise

    @staticmethod
    def public_run(run):
        result = {
            k: run[k] for k in ("runId", "state", "createdAt", "updatedAt", "error")
        }
        if result["state"] == "waiting":
            result["state"] = "running"
        result.update(
            resourceKind=run.get("resourceKind", "managedTable"),
            resourceId=run.get("resourceId", run.get("sourceId")),
        )
        for key in (
            "buildMode",
            "reused",
            "generationId",
            "measureRunId",
            "dimensionIndex",
            "dimensionIds",
            "childRunId",
            "queryMemoryBytes",
        ):
            if key in run:
                result[key] = run[key]
        if "sourceId" in run:
            result["sourceId"] = run["sourceId"]
        return result

    async def run_status(self, model, revision, run_id):
        run = await metadata_repositories(self.store.repository).runs.require(
            RunLookup(model_id=model, revision_id=revision, run_id=run_id), "Engine run"
        )
        return self.public_run(run)

    async def cancel(self, model, revision, run_id):
        async def operation(repo):
            run = await metadata_repositories(repo).runs.require(
                RunLookup(model_id=model, revision_id=revision, run_id=run_id),
                "Engine run",
            )
            if run["state"] in ("queued", "running", "waiting"):
                if run.get("resourceKind") in {"semanticNetwork", "dimensionSet"}:
                    # Cancel queued/running work owned by this command as well.
                    for key in (run.get("childRunId"), run["runId"] + "_values"):
                        if not key:
                            continue
                        child = await metadata_repositories(repo).runs.find(
                            RunLookup(run_id=key)
                            if key == run.get("childRunId")
                            else RunLookup(
                                model_id=model,
                                revision_id=revision,
                                idempotency_key=key,
                            )
                        )
                        if child and child["state"] in ("queued", "running", "waiting"):
                            await metadata_repositories(repo).runs.replace(
                                RunLookup(run_id=child["runId"], fence=child["fence"]),
                                {
                                    **child,
                                    "state": "cancelled",
                                    "fence": child["fence"] + 1,
                                    "updatedAt": now(),
                                },
                            )
                run = {
                    **run,
                    "state": "cancelled",
                    "fence": run["fence"] + 1,
                    "updatedAt": now(),
                }
                await metadata_repositories(repo).runs.replace(
                    RunLookup(model_id=model, revision_id=revision, run_id=run_id), run
                )
            return self.public_run(run)

        return await self.store.transaction(operation)

    async def disable(self, model, revision, source_id, expected):
        async def operation(repo):
            rev = await self.touch_revision(repo, model, revision)
            source = await self.source(repo, model, revision, source_id)
            managed = await metadata_repositories(repo).managed_tables.require(
                ManagedTableLookup(
                    model_id=model, revision_id=revision, source_id=source_id
                ),
                "Managed table",
            )
            self.check_version(managed, expected)
            updated = deepcopy(source)
            updated["definition"]["inScope"] = False
            updated.update(version=source["version"] + 1, updatedAt=now())
            objects = await self.graph_objects(repo, model, revision)
            effective = await EffectiveSchemaResolver.load(
                repo,
                model,
                revision,
                [updated if o["objectId"] == source_id else o for o in objects],
            )
            self.validate(effective, rev["baseDimensionId"])
            await metadata_repositories(repo).objects.replace(
                ObjectLookup(
                    model_id=model,
                    revision_id=revision,
                    object_id=source_id,
                    version=source["version"],
                ),
                updated,
            )
            await metadata_repositories(repo).managed_tables.replace(
                ManagedTableLookup(
                    model_id=model, revision_id=revision, source_id=source_id
                ),
                {**managed, "version": expected + 1, "updatedAt": now()},
            )

        await self.store.transaction(operation)

    async def schema(self, model, revision, source_id, payload):
        source = await self.source(self.store.repository, model, revision, source_id)
        logical = self.planner.plan(source, payload)
        return {"columns": list(logical.schema.columns)}

    async def preview(
        self, model, revision, source_id, payload=None, *, sql_only=False
    ):
        source = await self.source(self.store.repository, model, revision, source_id)
        table = None
        if payload is None:
            current = await self.get(model, revision, source_id)
            payload = self.input_definition(current["definition"])
            binding = await metadata_repositories(
                self.store.repository
            ).bindings.require(
                BindingLookup(
                    model_id=model, revision_id=revision, source_id=source_id
                ),
                "Active managed view; build this table first",
            )
            if binding["definitionHash"] != current["definitionHash"]:
                raise Conflict("The managed view is not ready for this definition.")
            artifact = await metadata_repositories(
                self.store.repository
            ).artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Artifact"
            )
            table = artifact["physicalName"]
        logical = self.planner.plan(source, payload)
        compiled = self.compiler.compile(logical)
        if sql_only:
            return {"sql": compiled.formatted_sql, "dialect": "clickhouse"}
        await self.executor.inspect(source)
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
            current = await metadata_repositories(repo).managed_tables.require(
                ManagedTableLookup(
                    model_id=model, revision_id=revision, source_id=source_id
                ),
                "Managed definition",
            )
            binding = await metadata_repositories(repo).bindings.require(
                BindingLookup(
                    model_id=model, revision_id=revision, source_id=source_id
                ),
                "Saved managed view; save this table first",
            )
            if binding["definitionHash"] != current["definitionHash"]:
                raise Conflict(
                    "Save and build the current managed view before discovering values."
                )
            artifact = await metadata_repositories(repo).artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Managed view"
            )
            if artifact["state"] != "ready":
                raise Conflict("The managed view is no longer ready. Retry discovery.")
            await metadata_repositories(repo).artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {**artifact, "pinVersion": artifact.get("pinVersion", 0) + 1},
            )
            await metadata_repositories(repo).read_leases.insert(
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
                }
            )
            return source, current, artifact

        source, current, artifact = await self.store.transaction(capture)
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
                lease = await metadata_repositories(repo).read_leases.find(
                    ReadLeaseLookup(read_id=read_id, artifact_id=artifact["artifactId"])
                )
                binding = await metadata_repositories(repo).bindings.find(
                    BindingLookup(
                        model_id=model, revision_id=revision, source_id=source_id
                    )
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
            await metadata_repositories(self.store.repository).read_leases.delete(
                ReadLeaseLookup(read_id=read_id, artifact_id=artifact["artifactId"])
            )

    async def preflight(self, model, revision):
        """Check outside Mongo transaction, then publication must compare revision version."""
        repo = self.store.repository
        rev = await metadata_repositories(repo).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
        )
        if rev["metadataContractVersion"] < 2:
            return rev["version"]
        for source in await self.graph_objects(repo, model, revision):
            if source["kind"] != "source" or not source["definition"]["inScope"]:
                continue
            current = await self.get(model, revision, source["objectId"])
            binding = await metadata_repositories(repo).bindings.require(
                BindingLookup(
                    model_id=model, revision_id=revision, source_id=source["objectId"]
                ),
                "Ready managed view",
            )
            if binding["definitionHash"] != current["definitionHash"]:
                raise Conflict("Build all managed views before publication.")
            await self.executor.inspect(source)
            compiled = self.compiler.compile(
                self.planner.plan(source, self.input_definition(current["definition"]))
            )
            artifact = await metadata_repositories(repo).artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Managed artifact"
            )
            await self.executor.verify(artifact, compiled)
            await self.executor.checks(compiled)
        return rev["version"]

    async def upgrade_contract(self, model, revision, expected):
        async def operation(repo):
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            self.check_version(rev, expected)
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            for source in await self.graph_objects(repo, model, revision):
                if source["kind"] == "source" and source["definition"]["inScope"]:
                    current = await metadata_repositories(repo).managed_tables.require(
                        ManagedTableLookup(
                            model_id=model,
                            revision_id=revision,
                            source_id=source["objectId"],
                        ),
                        "Initialized managed definition",
                    )
                    binding = await metadata_repositories(repo).bindings.require(
                        BindingLookup(
                            model_id=model,
                            revision_id=revision,
                            source_id=source["objectId"],
                        ),
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
            await metadata_repositories(repo).revisions.replace(
                RevisionLookup(model_id=model, revision_id=revision, version=expected),
                updated,
            )
            return updated

        return await self.store.transaction(operation)
