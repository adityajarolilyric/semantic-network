"""Retain captured artifacts while bounded warehouse reads are in flight."""

from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ArtifactLookup, ReadLeaseLookup

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from ..core.errors import Conflict, NotFound
from ..core.metadata import utc_now as now


class ArtifactReadLeases:
    duration = timedelta(seconds=180)
    renewal_interval_seconds = 15

    def __init__(self, store):
        self.store = store

    async def acquire(
        self, repository, read_id, artifacts, *, resource, retired_message
    ):
        # Touch the artifact in the same transaction as lease acquisition so
        # retention and retirement cannot both win their race.
        for artifact in {item["artifactId"]: item for item in artifacts}.values():
            identity = {"artifactId": artifact["artifactId"]}
            current = await metadata_repositories(repository).artifacts.find(
                ArtifactLookup(artifact_id=artifact["artifactId"])
            )
            if current is None:
                raise NotFound(resource)
            if current["state"] != "ready":
                raise Conflict(retired_message)
            await metadata_repositories(repository).artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {**current, "pinVersion": current.get("pinVersion", 0) + 1},
            )
            await metadata_repositories(repository).read_leases.insert(
                {"readId": read_id, **identity, "expiresAt": now() + self.duration}
            )

    async def verify(self, repository, read_id, artifact_ids, expired_message):
        for artifact_id in artifact_ids:
            lease = await metadata_repositories(repository).read_leases.find(
                ReadLeaseLookup(read_id=read_id, artifact_id=artifact_id)
            )
            if not lease or lease["expiresAt"] <= now():
                raise Conflict(expired_message)

    async def renew(self, read_id, artifact_ids, expired_message):
        while True:
            await asyncio.sleep(self.renewal_interval_seconds)

            async def extend(repository):
                await self.verify(repository, read_id, artifact_ids, expired_message)
                for artifact_id in artifact_ids:
                    lease = await metadata_repositories(repository).read_leases.find(
                        ReadLeaseLookup(read_id=read_id, artifact_id=artifact_id)
                    )
                    await metadata_repositories(repository).read_leases.replace(
                        ReadLeaseLookup(read_id=read_id, artifact_id=artifact_id),
                        {**lease, "expiresAt": now() + self.duration},
                    )

            await self.store.transaction(extend)

    async def release(self, read_id, artifact_ids):
        async def remove(repository):
            for artifact_id in artifact_ids:
                await metadata_repositories(repository).read_leases.delete(
                    ReadLeaseLookup(read_id=read_id, artifact_id=artifact_id)
                )

        await self.store.transaction(remove)

    @asynccontextmanager
    async def hold(self, read_id, artifact_ids, expired_message):
        heartbeat = asyncio.create_task(
            self.renew(read_id, artifact_ids, expired_message)
        )
        try:
            yield heartbeat
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat
            # The caller's workspace lease remains held through cleanup.
            await self.release(read_id, artifact_ids)
