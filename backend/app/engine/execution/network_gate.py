"""Cross-process admission barrier for in-place Semantic Network builds."""

import asyncio
from time import monotonic

from ...core.errors import Conflict, MetadataError
from ...core.metadata import utc_now
from ...repositories.metadata import metadata_repositories
from ...repositories.metadata.criteria import ReadLeaseLookup, RevisionLookup, RunLookup


class NetworkBuildGate:
    def __init__(self, store):
        self.store = store

    async def latest(self, repo, model, revision):
        runs = await metadata_repositories(repo).runs.list(
            RunLookup(
                model_id=model, revision_id=revision, resource_kind="semanticNetwork"
            ),
            order_by="createdAt",
            newest_first=True,
            limit=1,
        )
        return runs[0] if runs else None

    @staticmethod
    def recovery_required(run):
        return bool(run and run.get("writesStarted") and run["state"] != "succeeded")

    async def admit_read(self, repo, model, revision):
        repositories = metadata_repositories(repo)
        lookup = RevisionLookup(model_id=model, revision_id=revision)
        document = await repositories.revisions.require(lookup, "Revision")
        # Serialize admission with build submission, without changing the semantic
        # revision version or fingerprint. The read lease is acquired in this UoW.
        await repositories.revisions.replace(
            lookup, {**document, "updatedAt": utc_now()}
        )
        active = await repositories.runs.list(
            RunLookup(
                model_id=model,
                revision_id=revision,
                state_in=("queued", "running", "waiting"),
            ),
            order_by="createdAt",
            limit=1001,
        )
        if any(
            r.get("resourceKind") in {"measureSet", "dimension", "objectView"}
            for r in active
        ):
            raise MetadataError(
                "semantic_network_unavailable",
                "A semantic table is being built. Queries and edits are unavailable until it completes.",
                503,
            )
        publishing = await repositories.runs.list(
            RunLookup(model_id=model, revision_id=revision, publication_pending=True),
            limit=1,
        )
        if publishing:
            raise MetadataError(
                "semantic_network_unavailable",
                "A dimension preview is being saved. Retry after it completes.",
                503,
            )
        run = await self.latest(repo, model, revision)
        if run and (
            run["state"] in {"queued", "running", "waiting"}
            or self.recovery_required(run)
        ):
            raise MetadataError(
                "semantic_network_unavailable",
                "Semantic Network is being built. Queries and edits are unavailable until a successful build completes."
                if run["state"] in {"queued", "running", "waiting"}
                else "Semantic Network build did not complete. Build again to recover before querying or editing.",
                503,
            )

    async def drain(self):
        deadline = monotonic() + 190
        while await metadata_repositories(self.store.repository).read_leases.list(
            ReadLeaseLookup(expires_after=utc_now()), order_by="readId", limit=1
        ):
            if monotonic() >= deadline:
                raise Conflict(
                    "Existing reads have not finished. Retry the network build."
                )
            await asyncio.sleep(0.1)

    async def mark_writing(self, repo, run):
        root_id = run.get("networkRunId")
        if not root_id:
            raise Conflict(
                "In-place writes require an exclusive Semantic Network build."
            )
        repositories = metadata_repositories(repo)
        root = await repositories.runs.require(
            RunLookup(run_id=root_id), "Network build"
        )
        if root["state"] not in {"running", "waiting"}:
            raise Conflict("The network build was cancelled or superseded.")
        await repositories.runs.replace(
            RunLookup(run_id=root_id), {**root, "writesStarted": True}
        )
