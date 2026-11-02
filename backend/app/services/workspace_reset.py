import asyncio
from contextlib import suppress
from uuid import uuid4

from ..db.schema import CollectionSchemas
from .base import now


class WorkspaceResetService:
    def __init__(self, store, warehouse, workspace, gate):
        self.store, self.warehouse, self.workspace, self.gate = (
            store,
            warehouse,
            workspace,
            gate,
        )

    async def reset(self):
        owner = uuid4().hex
        await self.gate.begin_reset(owner)
        heartbeat = asyncio.create_task(self.gate.renew_reset(owner))
        try:

            async def cancel(repo):
                await self.gate.require_owner(repo, owner)
                cursor = None
                while True:
                    runs = await repo.list(
                        "engine_runs",
                        {"state": {"$in": ["queued", "running"]}},
                        key="runId",
                        after=cursor,
                        limit=100,
                    )
                    if not runs:
                        break
                    for run in runs:
                        await repo.replace(
                            "engine_runs",
                            {"runId": run["runId"]},
                            {
                                **run,
                                "state": "cancelled",
                                "fence": run.get("fence", 0) + 1,
                                "updatedAt": now(),
                            },
                        )
                    cursor = runs[-1]["runId"]

            await self.gate.internal(cancel)
            await self.gate.drain()
            tables = await self.warehouse.reset()

            async def clear(repo):
                state = await self.gate.require_owner(repo, owner)
                for collection in CollectionSchemas.REQUIRED:
                    if collection != "workspace_control":
                        await repo.delete_all(collection)
                model = await self.workspace.initialize(repo)
                await repo.replace(
                    "workspace_control",
                    {"controlId": "workspace"},
                    {**state, "phase": "ready", "owner": None, "leaseUntil": now()},
                )
                return model

            model = await self.gate.internal(clear)
            return {"model": model, "deletedTables": tables}
        except BaseException:

            async def release(repo):
                state = await self.gate.state(repo)
                if state.get("owner") == owner:
                    # Keep maintenance active after partial failure; retry can finish safely.
                    await repo.replace(
                        "workspace_control",
                        {"controlId": "workspace"},
                        {**state, "owner": None, "leaseUntil": now()},
                    )

            await self.gate.internal(release)
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
