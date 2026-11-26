"""Prepare views outside Mongo transactions; commit their binding with object metadata."""

import asyncio
from contextlib import suppress
from datetime import timedelta
from graphlib import TopologicalSorter
import logging
from uuid import uuid4

from ...core.errors import Conflict, MetadataError
from ...core.metadata import revision_scope, utc_now
from ...repositories.metadata import metadata_repositories
from ...repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ObjectLookup,
    RevisionLookup,
    RunLookup,
)
from ...schemas.dimensions import DimensionInput
from ...schemas.transactions import TransactionInput
from ..execution.network_gate import NetworkBuildGate
from ..execution.read_leases import ArtifactReadLeases
from ..execution.artifacts.recovery import ArtifactRecoveryLease
from .contracts import definition_hash
from .identity_inputs import IdentityInputReferences
from .dependencies import input_tokens, is_current, references


class ObjectViewService:
    def __init__(self, store, planner, storage, worker, dimensions, transactions):
        self.store, self.planner, self.storage, self.worker = (
            store,
            planner,
            storage,
            worker,
        )
        self.dimensions, self.transactions = dimensions, transactions
        self.gate = NetworkBuildGate(store)
        self.leases = ArtifactReadLeases(store)
        self.recovery_lease = ArtifactRecoveryLease(store)
        self.identity_inputs = IdentityInputReferences()

    async def save(
        self,
        service,
        model,
        revision,
        payload,
        expected,
        operation,
        network_run_id=None,
    ):
        await self.recover(model, revision)

        async def capture(repo):
            repositories = metadata_repositories(repo)
            rev = await repositories.revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            # Retained contract-1 metadata workspaces have no warehouse lifecycle.
            managed = await repositories.bindings.list(
                BindingLookup(
                    model_id=model, revision_id=revision, resource_kind="managedTable"
                ),
                limit=10001,
            )
            if not managed:
                return None
            objects = await service.definitions(repo, model, revision)
            previous = next(
                (o for o in objects if o["objectId"] == payload.objectId), None
            )
            if expected is not None:
                if not previous:
                    raise Conflict("The object no longer exists.")
                service.check_version(previous, expected)
            document = {
                **service.create_input(payload),
                **revision_scope(model, revision),
                "version": (expected or 0) + 1,
                "createdAt": (previous or {}).get("createdAt", utc_now()),
                "updatedAt": utc_now(),
            }
            document = service.prepare_document(
                document, await service.effective(repo, model, revision, objects)
            )
            candidates = [
                o for o in objects if o["objectId"] != document["objectId"]
            ] + [document]
            effective = await service.effective(repo, model, revision, candidates)
            related = (
                service.related_update_documents(previous, document, effective)
                if previous
                else service.additional_create_documents(payload, document, effective)
            )
            replaced = {o["objectId"]: o for o in related}
            effective = await service.effective(
                repo,
                model,
                revision,
                [replaced.get(o["objectId"], o) for o in candidates]
                + [
                    o
                    for o in related
                    if o["objectId"] not in {d["objectId"] for d in candidates}
                ],
            )
            service.validate(effective, rev["baseDimensionId"])
            binding = await repositories.bindings.find(
                BindingLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="objectView",
                    resource_id=document["objectId"],
                )
            )
            artifact = (
                await repositories.artifacts.require(
                    ArtifactLookup(artifact_id=binding["artifactId"]),
                    "Saved object view",
                )
                if binding
                else None
            )
            source_bindings = {b["sourceId"]: b for b in managed}
            tokens = input_tokens(document, effective, source_bindings)
            identity_inputs = await self.identity_inputs.capture(
                repo, model, revision, tokens, effective
            )
            return (
                rev,
                document,
                artifact,
                tokens,
                is_current(artifact, document, effective, source_bindings)
                if artifact
                else False,
                identity_inputs,
            )

        captured = await self.store.transaction(capture)
        if captured is None:
            return await self.store.transaction(operation)
        (
            revision_document,
            document,
            previous,
            tokens,
            current_inputs,
            identity_inputs,
        ) = captured
        superseded = previous
        missing_name = None
        if previous and not await self.storage.exists(previous):
            missing_name = previous["physicalName"]
            previous = None
        if (
            previous
            and previous["definitionHash"] == definition_hash(document)
            and current_inputs
        ):
            try:
                await self.storage.verify(previous)
            except Conflict:
                # A missing view can be repaired by Save; an existing foreign view
                # is still protected by publication's ownership check.
                pass
            else:
                return await self.store.transaction(operation)
        run_id, artifact_id = "run_" + uuid4().hex, "artifact_" + uuid4().hex
        label = (
            "".join(
                c if c.isascii() and (c.isalnum() or c == "_") else "_"
                for c in document["name"]
            ).strip("_")[:60]
            or "object"
        )
        prefix = "dim" if document["kind"] == "dimension" else "trans"
        name = missing_name or f"{prefix}_{label}_{artifact_id[9:]}"
        if previous and previous.get("state") == "ready":
            name += "_build"
        artifact = {
            **revision_scope(model, revision),
            "artifactId": artifact_id,
            "runId": run_id,
            "resourceKind": "objectView",
            "resourceId": document["objectId"],
            "kind": "view",
            "storageMode": "object_view",
            "physicalName": name,
            "viewName": name,
            "state": "candidate",
            "cleanupPending": True,
            "attempt": 1,
            "createdAt": utc_now(),
            "definitionHash": definition_hash(document),
            "objectKind": document["kind"],
            **tokens,
            "identityInputs": identity_inputs,
        }
        source = document["definition"].get("sourceId") or document["definition"].get(
            "population", {}
        ).get("sourceId")
        if source:
            artifact["sourceId"] = source
        if superseded:
            artifact["supersededArtifactId"] = superseded["artifactId"]
        if previous:
            artifact["previousView"] = {
                k: previous[k] for k in ("artifactId", "physicalName")
            }
        run = {
            **revision_scope(model, revision),
            "runId": run_id,
            "resourceKind": "objectView",
            "resourceId": document["objectId"],
            "requestHash": artifact["definitionHash"],
            "idempotencyKey": "object_view_" + run_id,
            "artifactId": artifact_id,
            "state": "running",
            "fence": 1,
            "owner": self.worker.owner,
            "leaseUntil": utc_now() + timedelta(seconds=60),
            "createdAt": utc_now(),
            "updatedAt": utc_now(),
            "error": None,
        }

        if network_run_id:
            run["networkRunId"] = network_run_id

        async def reserve(repo):
            repositories = metadata_repositories(repo)
            rev = await repositories.revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            if rev["version"] != revision_document["version"]:
                raise Conflict("Metadata changed. Reload and save again.")
            active = await repositories.runs.list(
                RunLookup(
                    model_id=model,
                    revision_id=revision,
                    state_in=["queued", "running", "waiting"],
                ),
                limit=1001,
            )
            if any(
                r["resourceKind"]
                in {"semanticNetwork", "dimension", "measureSet", "objectView"}
                for r in active
                if r["runId"] != network_run_id
            ):
                raise Conflict(
                    "A save or network build is in progress. Retry after it completes."
                )
            # Write the same revision record as read/build admission.
            await repositories.revisions.replace(
                RevisionLookup(model_id=model, revision_id=revision),
                {**rev, "updatedAt": utc_now()},
            )
            await repositories.runs.insert(run)
            await repositories.artifacts.insert(artifact)

        await self.store.transaction(reserve)
        heartbeat = asyncio.create_task(self.worker.heartbeat(run))
        try:
            plan = await self.planner.compile(model, revision, document)
            if (
                len(plan.execution.sql.encode())
                > self.storage.warehouse.settings.measure_max_sql_bytes
            ):
                raise MetadataError(
                    "object_view_sql_limit",
                    "The transformed view exceeds the configured SQL size limit. Simplify its inputs or transformations.",
                )
            columns, count = await self.storage.create(artifact, plan)
            artifact.update(
                columns=columns,
                memberColumns=plan.member_columns,
                mappings=plan.mappings,
                rowCount=count,
                buildSql=plan.execution.formatted_sql,
                checks=[{"code": c.code, "sql": c.sql} for c in plan.execution.checks],
            )
            await self.journal(artifact)
            await self.gate.drain()
            await self.store.transaction(lambda repo: self.worker.fenced(repo, run))
            await self.storage.publish(artifact, self.journal)

            async def commit(repo):
                await self.worker.fenced(repo, run)
                repositories = metadata_repositories(repo)
                rev = await repositories.revisions.require(
                    RevisionLookup(model_id=model, revision_id=revision), "Revision"
                )
                if rev["version"] != revision_document["version"]:
                    raise Conflict(
                        "Metadata changed while saving the view. Retry the save."
                    )
                result = await operation(repo)
                await self.identity_inputs.apply(repo, run, identity_inputs)
                identity = {
                    **revision_scope(model, revision),
                    "resourceKind": "objectView",
                    "resourceId": document["objectId"],
                }
                artifact["state"] = "ready"
                await repositories.artifacts.replace(
                    ArtifactLookup(artifact_id=artifact_id), artifact
                )
                await repositories.activation.bind(
                    identity,
                    {
                        **identity,
                        "artifactId": artifact_id,
                        "definitionHash": artifact["definitionHash"],
                    },
                )
                current = await self.worker.fenced(repo, run)
                await repositories.activation.complete(run, current)
                return result

            result = await self.store.transaction(commit)
        except BaseException:
            await self.storage.cleanup(artifact, rollback=True)
            artifact.update(state="dropped", cleanupPending=False, droppedAt=utc_now())
            await self.journal(artifact)

            async def fail(repo):
                current = await metadata_repositories(repo).runs.require(
                    RunLookup(run_id=run_id), "Object save"
                )
                await metadata_repositories(repo).runs.replace(
                    RunLookup(run_id=run_id),
                    {**current, "state": "failed", "updatedAt": utc_now()},
                )

            await self.store.transaction(fail)
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        try:
            await self.finish(artifact)
        except Exception:
            logging.getLogger(__name__).exception("object_view_cleanup_deferred")
        return result

    async def journal(self, artifact):
        await metadata_repositories(self.store.repository).artifacts.replace(
            ArtifactLookup(artifact_id=artifact["artifactId"]), artifact
        )

    async def finish(self, artifact):
        await self.storage.cleanup(artifact)
        previous_id = artifact.get("supersededArtifactId") or (
            artifact.get("previousView") or {}
        ).get("artifactId")
        if previous_id:
            previous = await metadata_repositories(
                self.store.repository
            ).artifacts.require(
                ArtifactLookup(artifact_id=previous_id),
                "Previous object view",
            )
            await metadata_repositories(self.store.repository).artifacts.replace(
                ArtifactLookup(artifact_id=previous["artifactId"]),
                {
                    **previous,
                    "state": "dropped",
                    "cleanupPending": False,
                    "droppedAt": utc_now(),
                },
            )
        artifact.update(cleanupPending=False)
        await self.journal(artifact)

    async def recover(self, model, revision):
        repositories = metadata_repositories(self.store.repository)
        pending = await repositories.artifacts.list(
            ArtifactLookup(
                model_id=model,
                revision_id=revision,
                storage_mode="object_view",
                cleanup_pending=True,
            ),
            limit=1001,
        )
        for candidate in pending:
            run = await repositories.runs.require(
                RunLookup(run_id=candidate["runId"]), "Object-view save"
            )
            if (
                run["state"] in {"queued", "running", "waiting"}
                and run["leaseUntil"] > utc_now()
                and run["fence"] <= candidate["attempt"]
            ):
                continue
            async with self.recovery_lease.hold(candidate["artifactId"]) as artifact:
                if not artifact:
                    continue
                if run["state"] == "succeeded":
                    await self.finish(artifact)
                else:
                    await self.storage.cleanup(artifact, rollback=True)
                    artifact.update(
                        state="dropped", cleanupPending=False, droppedAt=utc_now()
                    )
                    await self.journal(artifact)

    async def ensure_saved(self, document, network_run_id=None):
        model, revision = document["modelId"], document["revisionId"]
        service = (
            self.dimensions if document["kind"] == "dimension" else self.transactions
        )
        cls = DimensionInput if document["kind"] == "dimension" else TransactionInput
        payload = cls.model_validate(
            {
                k: v
                for k, v in document.items()
                if k
                not in {
                    "modelId",
                    "revisionId",
                    "createdAt",
                    "updatedAt",
                    "version",
                    "_id",
                }
            }
        )

        async def unchanged(repo):
            return await metadata_repositories(repo).objects.require(
                ObjectLookup(
                    model_id=model,
                    revision_id=revision,
                    object_id=document["objectId"],
                    version=document["version"],
                ),
                "Saved definition",
            )

        return await self.save(
            service,
            model,
            revision,
            payload,
            document["version"],
            unchanged,
            network_run_id=network_run_id,
        )

    async def preview(self, model, revision, object_id, sql_only=False):
        repositories = metadata_repositories(self.store.repository)
        document = await repositories.objects.require(
            ObjectLookup(model_id=model, revision_id=revision, object_id=object_id),
            "Saved object",
        )
        binding = await repositories.bindings.find(
            BindingLookup(
                model_id=model,
                revision_id=revision,
                resource_kind="objectView",
                resource_id=object_id,
            )
        )
        if not binding or binding["definitionHash"] != definition_hash(document):
            raise Conflict("Save this object before previewing its transformed view.")
        artifact = await repositories.artifacts.require(
            ArtifactLookup(artifact_id=binding["artifactId"]), "Saved view"
        )
        objects = await self.dimensions.definitions(
            self.store.repository, model, revision
        )
        sources = {
            b["sourceId"]: b
            for b in await repositories.bindings.list(
                BindingLookup(
                    model_id=model, revision_id=revision, resource_kind="managedTable"
                ),
                limit=10001,
            )
        }
        if not is_current(artifact, document, objects, sources):
            raise Conflict(
                "An input definition changed. Save this object before Preview, or rebuild the Semantic Network."
            )
        if sql_only:
            return {"sql": self.storage.read_sql(artifact), "dialect": "clickhouse"}
        read_id = "read_" + uuid4().hex

        async def admit(repo):
            await self.gate.admit_read(repo, model, revision)
            await self.leases.acquire(
                repo,
                read_id,
                [artifact],
                resource="Object view",
                retired_message="The saved view changed. Retry Preview.",
            )

        await self.store.transaction(admit)
        async with self.leases.hold(
            read_id, [artifact["artifactId"]], "Object preview expired. Retry."
        ):
            return await self.storage.preview(artifact)

    async def prepare_network(self, run):
        objects = await self.dimensions.definitions(
            self.store.repository, run["modelId"], run["revisionId"]
        )
        documents = {
            o["objectId"]: o
            for o in objects
            if o["kind"] in {"dimension", "transaction"}
        }
        graph = TopologicalSorter(
            {
                oid: references(o, objects) & documents.keys()
                for oid, o in documents.items()
            }
        )
        for oid in graph.static_order():
            await self.ensure_saved(documents[oid], network_run_id=run["runId"])

    async def execute_run(self, run, worker):
        await self.recover(run["modelId"], run["revisionId"])
        raise Conflict(
            "An interrupted object save was rolled back. Save the object again."
        )
