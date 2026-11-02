import asyncio
from contextlib import suppress
from datetime import timedelta
import logging
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..schemas.managed_tables import ManagedDefinition
from ..services.base import now, scope
from .contracts import digest
from .artifacts import ArtifactCollector
from .resolver import EffectiveSchemaResolver

logger = logging.getLogger("metadata.engine")


class EngineWorker:
    """Durable Mongo queue, renewable leases and fenced activation across API workers."""

    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.owner = uuid4().hex
        self.task = None
        self.last_collection = now()
        self.collector = ArtifactCollector(service)

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def loop(self):
        while True:
            try:
                async with self.service.workspace_gate.shared():
                    run = await self.claim()
                    if run:
                        await self.execute(run)
                    else:
                        if now() - self.last_collection > timedelta(minutes=10):
                            await self.collector.collect()
                            self.last_collection = now()
                        await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except MetadataError as exc:
                if exc.code != "workspace_resetting":
                    logger.exception("engine_worker_iteration_failed")
                await asyncio.sleep(0.5)
            except Exception:
                logger.exception("engine_worker_iteration_failed")
                await asyncio.sleep(2)

    async def claim(self):
        # Indexed state partitions; never load historical terminal runs.
        repo = self.store.repository
        for state in ("queued", "running"):
            rows = await repo.list(
                "engine_runs",
                {
                    "state": state,
                    **({"leaseUntil": {"$lt": now()}} if state == "running" else {}),
                },
                key="runId",
                limit=100,
            )
            for candidate in rows:
                if state == "running" and candidate["leaseUntil"] > now():
                    continue

                async def operation(repo):
                    current = await repo.get(
                        "engine_runs", {"runId": candidate["runId"]}
                    )
                    if current["state"] != state or (
                        state == "running" and current["leaseUntil"] > now()
                    ):
                        return None
                    updated = {
                        **current,
                        "state": "running",
                        "owner": self.owner,
                        "fence": current["fence"] + 1,
                        "leaseUntil": now() + timedelta(seconds=60),
                        "updatedAt": now(),
                    }
                    if (
                        current.get("resourceKind")
                        in ("planningLevel", "planningLevelPreview")
                        and current["fence"] > 0
                    ):
                        # Each recovered attempt writes a separate row generation in the same table.
                        previous = await repo.get(
                            "engine_artifacts", {"artifactId": current["artifactId"]}
                        )
                        aid = "artifact_" + uuid4().hex
                        await repo.insert(
                            "engine_artifacts",
                            {
                                **previous,
                                "artifactId": aid,
                                "physicalName": previous["physicalName"],
                                "generationToken": aid,
                                "attempt": updated["fence"],
                                "state": "candidate",
                                "createdAt": now(),
                            },
                        )
                        updated["artifactId"] = aid
                    matched = await repo.replace(
                        "engine_runs",
                        {
                            "runId": current["runId"],
                            "state": state,
                            "fence": current["fence"],
                        },
                        updated,
                    )
                    return updated if matched else None

                claimed = await self.store.transaction(operation)
                if claimed:
                    return claimed
        return None

    async def heartbeat(self, run):
        while True:
            await asyncio.sleep(10)

            async def operation(repo):
                current = await self.fenced(repo, run)
                await repo.replace(
                    "engine_runs",
                    {"runId": run["runId"], "fence": run["fence"]},
                    {
                        **current,
                        "leaseUntil": now() + timedelta(seconds=60),
                        "updatedAt": now(),
                    },
                )

            await self.store.transaction(operation)

    async def fenced(self, repo, run):
        current = await repo.get("engine_runs", {"runId": run["runId"]})
        if (
            current["state"] != "running"
            or current["fence"] != run["fence"]
            or current["owner"] != self.owner
            or current["leaseUntil"] <= now()
        ):
            raise Conflict("Engine lease expired or the run was cancelled.")
        return current

    async def step(self, run, step_id, state, **details):
        async def operation(repo):
            await self.fenced(repo, run)
            query = {"runId": run["runId"], "stepId": step_id}
            current = await repo.get("engine_run_steps", query)
            document = {
                **query,
                "state": state,
                "attempt": run["fence"],
                "updatedAt": now(),
                **details,
            }
            if current:
                await repo.replace("engine_run_steps", query, document)
            else:
                await repo.insert("engine_run_steps", document)

        await self.store.transaction(operation)

    async def execute(self, run):
        heartbeat = asyncio.create_task(self.heartbeat(run))
        try:
            service = self.service
            if run.get("resourceKind") == "measureSet":
                await service.measure_builds.execute_run(run, self)
                return
            if run.get("resourceKind") in ("planningLevel", "planningLevelPreview"):
                await service.planning_builds.execute_run(run, self)
                return
            inputs = await self.store.repository.list(
                "engine_run_inputs",
                {"runId": run["runId"]},
                key="inputId",
                limit=service.max_objects,
            )
            objects = [i["document"] for i in inputs]
            source = next(o for o in objects if o["objectId"] == run["sourceId"])
            plan = service.planner.plan(
                source,
                ManagedDefinition.model_validate(
                    {k: v for k, v in run["definition"].items() if k != "columns"}
                ),
            )
            compiled = service.compiler.compile(plan)
            await self.step(
                run,
                "compile",
                "succeeded",
                sql=compiled.sql,
                planHash=digest(compiled.sql),
                compilerVersion=compiled.compiler_version,
            )
            fingerprint = await service.executor.inspect(source)
            await self.step(run, "checks", "running")
            await service.executor.checks(compiled)
            await self.step(run, "checks", "succeeded")
            artifact = await service.require(
                self.store.repository,
                "engine_artifacts",
                {"artifactId": run["artifactId"]},
                "Artifact",
            )
            await self.step(run, "create_view", "running")
            await service.executor.create(artifact, compiled)
            await self.step(run, "create_view", "succeeded")
            # Repeat checks after DDL and schema inspection, before entering the activation transaction.
            if fingerprint != await service.executor.inspect(source):
                raise Conflict("The raw schema changed during execution.")
            await service.executor.checks(compiled)
            await self.activate(run, artifact, fingerprint, compiled.compiler_version)
        except asyncio.CancelledError:
            # Leave durable state recoverable after its lease expires.
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, MetadataError)
                else "engine_execution_failed"
            )
            message = (
                exc.message
                if isinstance(exc, MetadataError)
                else "The build failed. Retry after checking the warehouse connection."
            )
            failed_state = "superseded" if isinstance(exc, Conflict) else "failed"
            error_details = getattr(exc, "details", None)

            async def fail(repo):
                current = await repo.get("engine_runs", {"runId": run["runId"]})
                if current["state"] == "running" and current["fence"] == run["fence"]:
                    await repo.replace(
                        "engine_runs",
                        {"runId": run["runId"], "fence": run["fence"]},
                        {
                            **current,
                            "state": failed_state,
                            "updatedAt": now(),
                            "error": {
                                "code": code,
                                "message": message,
                                "details": error_details,
                            },
                        },
                    )
                    if run.get("isEdit"):
                        await repo.delete(
                            "engine_run_inputs",
                            {"runId": run["runId"], "inputId": "__edit"},
                        )

            await self.store.transaction(fail)
            if not isinstance(exc, MetadataError):
                logger.exception("engine_run_failed")
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Conflict):
                await heartbeat

    async def activate(self, run, artifact, fingerprint, compiler_version):
        service = self.service

        async def operation(repo):
            current_run = await self.fenced(repo, run)
            query = scope(run["modelId"], run["revisionId"])
            revision = await service.require(
                repo, "semantic_revisions", query, "Revision"
            )
            service.check_version(revision, run["revisionVersion"])
            if not run["repair"] and revision["approvalStatus"] != "draft":
                raise Conflict("The revision was published while this build ran.")
            source = await service.source(
                repo, run["modelId"], run["revisionId"], run["sourceId"]
            )
            identity = {**query, "sourceId": run["sourceId"]}
            old = await repo.get("semantic_managed_tables", identity)
            if (old["version"] if old else 0) != run["expectedVersion"]:
                raise Conflict()
            document = {
                **identity,
                "definition": run["definition"],
                "definitionHash": run["definitionHash"],
                "schemaVersion": 1,
                "version": run["expectedVersion"] + 1,
                "createdAt": old["createdAt"] if old else now(),
                "updatedAt": now(),
            }
            if run["repair"]:
                if old is None:
                    await repo.insert("semantic_managed_tables", document)
                else:
                    document = old
            else:
                objects = await service.objects(repo, run["modelId"], run["revisionId"])
                managed = await repo.list(
                    "semantic_managed_tables",
                    query,
                    key="sourceId",
                    limit=service.max_objects,
                )
                effective = EffectiveSchemaResolver.apply(
                    objects,
                    [d for d in managed if d["sourceId"] != run["sourceId"]]
                    + [document],
                )
                for obj in effective:
                    if obj["objectId"] == run["sourceId"]:
                        obj["definition"]["inScope"] = True
                service.validate(effective, revision["basePlanningLevelId"])
                if old:
                    await repo.replace(
                        "semantic_managed_tables",
                        {**identity, "version": old["version"]},
                        document,
                    )
                else:
                    await repo.insert("semantic_managed_tables", document)
                source = {
                    **source,
                    "definition": {**source["definition"], "inScope": True},
                    "version": source["version"] + 1,
                    "updatedAt": now(),
                }
                await repo.replace(
                    "semantic_objects",
                    {**query, "objectId": source["objectId"]},
                    source,
                )
                await service.touch_revision(repo, run["modelId"], run["revisionId"])
            binding = {
                **identity,
                "resourceKind": "managedTable",
                "resourceId": run["sourceId"],
                "artifactId": artifact["artifactId"],
                "definitionHash": run["definitionHash"],
            }
            if await repo.get("engine_artifact_bindings", identity):
                await repo.replace("engine_artifact_bindings", identity, binding)
            else:
                await repo.insert("engine_artifact_bindings", binding)
            await repo.replace(
                "engine_artifacts",
                {"artifactId": artifact["artifactId"]},
                {
                    **artifact,
                    "state": "ready",
                    "sourceFingerprint": fingerprint,
                    "compilerVersion": compiler_version,
                },
            )
            await repo.replace(
                "engine_runs",
                {"runId": run["runId"], "fence": run["fence"]},
                {**current_run, "state": "succeeded", "updatedAt": now()},
            )

        await self.store.transaction(operation)
