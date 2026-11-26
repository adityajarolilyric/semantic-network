"""Serialize reversible publication recovery across API/worker processes."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from ....core.errors import Conflict
from ....core.metadata import utc_now
from ....repositories.metadata import metadata_repositories
from ....repositories.metadata.criteria import ArtifactLookup


class ArtifactRecoveryLease:
    def __init__(self, store):
        self.store = store

    @asynccontextmanager
    async def hold(self, artifact_id):
        owner = uuid4().hex
        deadline = monotonic() + 190
        while True:

            async def claim(repo):
                artifacts = metadata_repositories(repo).artifacts
                document = await artifacts.require(
                    ArtifactLookup(artifact_id=artifact_id), "Build candidate"
                )
                if not document.get("cleanupPending"):
                    return False
                if (
                    document.get("cleanupOwner")
                    and document.get("cleanupLeaseUntil", utc_now()) > utc_now()
                ):
                    return None
                updated = {
                    **document,
                    "cleanupOwner": owner,
                    "cleanupLeaseUntil": utc_now() + timedelta(seconds=90),
                }
                await artifacts.replace(
                    ArtifactLookup(artifact_id=artifact_id), updated
                )
                return updated

            document = await self.store.transaction(claim)
            if document is not None:
                break
            if monotonic() >= deadline:
                raise Conflict(
                    "Another worker is recovering this table. Retry shortly."
                )
            await asyncio.sleep(0.1)
        if document is False:
            yield None
            return

        async def renew():
            while True:
                await asyncio.sleep(15)

                async def update(repo):
                    artifacts = metadata_repositories(repo).artifacts
                    current = await artifacts.require(
                        ArtifactLookup(artifact_id=artifact_id), "Build candidate"
                    )
                    if current.get("cleanupOwner") != owner:
                        raise Conflict("The publication recovery lease was superseded.")
                    await artifacts.replace(
                        ArtifactLookup(artifact_id=artifact_id),
                        {
                            **current,
                            "cleanupLeaseUntil": utc_now() + timedelta(seconds=90),
                        },
                    )

                await self.store.transaction(update)

        heartbeat = asyncio.create_task(renew())
        try:
            yield document
            if heartbeat.done():
                heartbeat.result()
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

            async def release(repo):
                artifacts = metadata_repositories(repo).artifacts
                current = await artifacts.require(
                    ArtifactLookup(artifact_id=artifact_id), "Build candidate"
                )
                if current.get("cleanupOwner") == owner:
                    await artifacts.replace(
                        ArtifactLookup(artifact_id=artifact_id),
                        {
                            **current,
                            "cleanupOwner": None,
                            "cleanupLeaseUntil": utc_now(),
                        },
                    )

            await self.store.transaction(release)
