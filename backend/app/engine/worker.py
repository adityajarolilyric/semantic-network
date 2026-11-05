from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    RunInputLookup,
    RunLookup,
    StepLookup,
    UNSET,
)
import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..core.metadata import utc_now as now
from .artifacts import ArtifactCollector
from .jobs import EngineRunHandler
from .managed_builds import ManagedTableBuildHandler

logger = logging.getLogger("metadata.engine")


class EngineWorker:
    """Durable Mongo queue, renewable leases and fenced activation across API workers."""

    def __init__(self, service, handlers: dict[str, EngineRunHandler] | None = None):
        self.handlers = {
            "managedTable": ManagedTableBuildHandler(service),
            **(handlers or {}),
        }
        self.service = service
        self.store = service.store
        self.owner = uuid4().hex
        self.task = None
        self.last_collection = now()
        self.collector = ArtifactCollector(service)

    def register(self, resource_kind: str, handler: EngineRunHandler):
        self.handlers[resource_kind] = handler

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
            rows = await metadata_repositories(repo).runs.list(
                RunLookup(
                    state=state,
                    lease_until_before=now() if state == "running" else UNSET,
                ),
                order_by="runId",
                limit=100,
            )
            for candidate in rows:
                if state == "running" and candidate["leaseUntil"] > now():
                    continue

                async def operation(repo):
                    current = await metadata_repositories(repo).runs.find(
                        RunLookup(run_id=candidate["runId"])
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
                        previous = await metadata_repositories(repo).artifacts.find(
                            ArtifactLookup(artifact_id=current["artifactId"])
                        )
                        aid = "artifact_" + uuid4().hex
                        await metadata_repositories(repo).artifacts.insert(
                            {
                                **previous,
                                "artifactId": aid,
                                "physicalName": previous["physicalName"],
                                "generationToken": aid,
                                "attempt": updated["fence"],
                                "state": "candidate",
                                "createdAt": now(),
                            }
                        )
                        updated["artifactId"] = aid
                    matched = await metadata_repositories(repo).runs.replace(
                        RunLookup(
                            run_id=current["runId"], state=state, fence=current["fence"]
                        ),
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
                await metadata_repositories(repo).runs.replace(
                    RunLookup(run_id=run["runId"], fence=run["fence"]),
                    {
                        **current,
                        "leaseUntil": now() + timedelta(seconds=60),
                        "updatedAt": now(),
                    },
                )

            await self.store.transaction(operation)

    async def fenced(self, repo, run):
        current = await metadata_repositories(repo).runs.find(
            RunLookup(run_id=run["runId"])
        )
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
            current = await metadata_repositories(repo).steps.find(
                StepLookup(run_id=run["runId"], step_id=step_id)
            )
            timestamp = now()
            same_attempt = current and current.get("attempt") == run["fence"]
            started = current.get("startedAt") if same_attempt else None
            if state == "running" and (
                not same_attempt or current["state"] != "running"
            ):
                started = timestamp
            document = {
                **query,
                "state": state,
                "attempt": run["fence"],
                "updatedAt": timestamp,
                **({"startedAt": started} if started else {}),
                **(
                    {
                        "durationMs": round(
                            (timestamp - started).total_seconds() * 1000, 3
                        )
                    }
                    if started and state != "running"
                    else {}
                ),
                **details,
            }
            if current:
                await metadata_repositories(repo).steps.replace(
                    StepLookup(run_id=run["runId"], step_id=step_id), document
                )
            else:
                await metadata_repositories(repo).steps.insert(document)

        await self.store.transaction(operation)

    async def execute(self, run):
        heartbeat = asyncio.create_task(self.heartbeat(run))
        try:
            resource_kind = run.get("resourceKind") or "managedTable"
            handler = self.handlers.get(resource_kind)
            if handler is None:
                raise MetadataError(
                    "unsupported_engine_target",
                    "This worker cannot execute the requested resource kind.",
                )
            await handler.execute_run(run, self)
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
                current = await metadata_repositories(repo).runs.find(
                    RunLookup(run_id=run["runId"])
                )
                if current["state"] == "running" and current["fence"] == run["fence"]:
                    await metadata_repositories(repo).runs.replace(
                        RunLookup(run_id=run["runId"], fence=run["fence"]),
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
                        await metadata_repositories(repo).inputs.delete(
                            RunInputLookup(run_id=run["runId"], input_id="__edit")
                        )

            await self.store.transaction(fail)
            if not isinstance(exc, MetadataError):
                logger.exception("engine_run_failed")
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Conflict):
                await heartbeat
