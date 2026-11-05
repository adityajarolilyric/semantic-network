"""Transaction-bound activation persistence, reused within the unit of work."""

from ...core.metadata import utc_now
from .criteria import BindingLookup, RunLookup


class ActivationWriter:
    def __init__(self, documents):
        from . import metadata_repositories

        self.repositories = metadata_repositories(documents)

    async def bind(self, identity, binding):
        lookup = BindingLookup.from_record(identity)
        if await self.repositories.bindings.find(lookup):
            await self.repositories.bindings.replace(lookup, binding)
        else:
            await self.repositories.bindings.insert(binding)

    async def complete(self, run, current, **result):
        await self.repositories.runs.replace(
            RunLookup(run_id=run["runId"], fence=run["fence"]),
            {**current, "state": "succeeded", "updatedAt": utc_now(), **result},
        )
