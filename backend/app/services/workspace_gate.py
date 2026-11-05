"""Cross-process maintenance barrier for API requests and engine work."""

from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    WorkspaceControlLookup,
    WorkspaceLeaseLookup,
)

import asyncio
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from .base import now

workspace_epoch = ContextVar("workspace_epoch", default=None)


class WorkspaceGate:
    def __init__(self, store):
        self.store = store
        store.transaction_guard = self.guard

    async def state(self, repo):
        state = await metadata_repositories(repo).workspace_control.find(
            WorkspaceControlLookup(control_id="workspace")
        )
        if not state:
            state = {
                "controlId": "workspace",
                "phase": "ready",
                "epoch": 0,
                "sequence": 0,
                "owner": None,
                "leaseUntil": now(),
            }
            await metadata_repositories(repo).workspace_control.insert(state)
        return state

    async def internal(self, operation):
        token = workspace_epoch.set(None)
        try:
            for attempt in range(3):
                try:
                    return await self.store.transaction(operation)
                except Conflict:
                    if attempt == 2:
                        raise
        finally:
            workspace_epoch.reset(token)

    async def guard(self, repo):
        epoch = workspace_epoch.get()
        if epoch is None:
            return
        state = await self.state(repo)
        if state["phase"] != "ready" or state["epoch"] != epoch:
            raise MetadataError(
                "workspace_resetting",
                "The workspace is being reset. Reload after it finishes.",
                503,
            )
        await metadata_repositories(repo).workspace_control.replace(
            WorkspaceControlLookup(control_id="workspace"),
            {**state, "sequence": state["sequence"] + 1},
        )

    async def heartbeat(self, lease_id):
        while True:
            await asyncio.sleep(10)

            async def renew(repo):
                lease = await metadata_repositories(repo).workspace_leases.find(
                    WorkspaceLeaseLookup(lease_id=lease_id)
                )
                if lease:
                    await metadata_repositories(repo).workspace_leases.replace(
                        WorkspaceLeaseLookup(lease_id=lease_id),
                        {**lease, "expiresAt": now() + timedelta(seconds=180)},
                    )

            await self.internal(renew)

    @asynccontextmanager
    async def shared(self):
        lease_id = uuid4().hex

        async def acquire(repo):
            state = await self.state(repo)
            if state["phase"] != "ready":
                raise MetadataError(
                    "workspace_resetting",
                    "Workspace reset is in progress or needs retrying from Sources.",
                    503,
                )
            await metadata_repositories(repo).workspace_control.replace(
                WorkspaceControlLookup(control_id="workspace"),
                {**state, "sequence": state["sequence"] + 1},
            )
            await metadata_repositories(repo).workspace_leases.insert(
                {
                    "leaseId": lease_id,
                    "epoch": state["epoch"],
                    "expiresAt": now() + timedelta(seconds=180),
                }
            )
            return state["epoch"]

        epoch = await self.internal(acquire)
        token = workspace_epoch.set(epoch)
        heartbeat = asyncio.create_task(self.heartbeat(lease_id))
        try:
            yield
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            workspace_epoch.reset(token)
            await self.internal(
                lambda repo: metadata_repositories(repo).workspace_leases.delete(
                    WorkspaceLeaseLookup(lease_id=lease_id)
                )
            )

    async def begin_reset(self, owner):
        async def acquire(repo):
            state = await self.state(repo)
            if (
                state["phase"] == "resetting"
                and state["owner"]
                and state["leaseUntil"] > now()
            ):
                raise Conflict("A workspace reset is already running.")
            await metadata_repositories(repo).workspace_control.replace(
                WorkspaceControlLookup(control_id="workspace"),
                {
                    **state,
                    "phase": "resetting",
                    "owner": owner,
                    "epoch": state["epoch"] + 1,
                    "leaseUntil": now() + timedelta(seconds=180),
                },
            )

        await self.internal(acquire)

    async def require_owner(self, repo, owner):
        state = await self.state(repo)
        if (
            state["phase"] != "resetting"
            or state["owner"] != owner
            or state["leaseUntil"] <= now()
        ):
            raise Conflict("Workspace reset ownership expired. Retry the reset.")
        return state

    async def renew_reset(self, owner):
        while True:
            await asyncio.sleep(10)

            async def renew(repo):
                state = await self.require_owner(repo, owner)
                await metadata_repositories(repo).workspace_control.replace(
                    WorkspaceControlLookup(control_id="workspace"),
                    {**state, "leaseUntil": now() + timedelta(seconds=180)},
                )

            await self.internal(renew)

    async def drain(self):
        deadline = monotonic() + 130
        while await metadata_repositories(self.store.repository).workspace_leases.list(
            WorkspaceLeaseLookup(expires_after=now()), order_by="leaseId", limit=1
        ):
            if monotonic() > deadline:
                raise Conflict(
                    "Existing operations are still finishing. Retry Nuke everything shortly."
                )
            await asyncio.sleep(0.2)
