"""Collect superseded source views without touching imported tables or live reads."""

from ..core.errors import Conflict
from ..core.metadata import utc_now
from ..engine.sql.storage import owner
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    PinLookup,
    ReadLeaseLookup,
    RunLookup,
)


class SourceViewRetirement:
    def __init__(self, storage):
        self.storage = storage
        self.warehouse = storage.warehouse

    async def finish(self, output):
        repositories = metadata_repositories(self.storage.store.repository)
        after = None
        while True:
            artifacts = await repositories.artifacts.list(
                ArtifactLookup(
                    model_id=output["modelId"],
                    revision_id=output["revisionId"],
                    source_id=output["sourceId"],
                    state_in=["candidate", "ready", "retiring"],
                ),
                order_by="artifactId",
                after=after,
                limit=100,
            )
            for artifact in artifacts:
                if (
                    artifact.get("storageMode") not in {None, "source"}
                    or artifact.get("cleanupPending")
                    or artifact.get("kind") == "table"
                    or artifact["physicalName"]
                    in {output["physicalName"], output["sourceTableName"]}
                ):
                    continue

                async def claim(repo):
                    current_repositories = metadata_repositories(repo)
                    current = await current_repositories.artifacts.require(
                        ArtifactLookup(artifact_id=artifact["artifactId"]),
                        "Superseded source view",
                    )
                    if (
                        current["physicalName"] != artifact["physicalName"]
                        or current.get("cleanupPending")
                        or current.get("storageMode") not in {None, "source"}
                        or not await self.unreferenced(current_repositories, current)
                    ):
                        return None
                    retiring = {**current, "state": "retiring"}
                    await current_repositories.artifacts.replace(
                        ArtifactLookup(artifact_id=current["artifactId"]), retiring
                    )
                    return retiring

                artifact = await self.storage.store.transaction(claim)
                if not artifact:
                    continue

                def drop(connect):
                    client = connect()
                    name = artifact["physicalName"]
                    if self.warehouse.table_info(client, name):
                        if not self.storage.owns_view(client, name, owner(artifact)):
                            raise Conflict(
                                "The superseded source view ownership changed."
                            )
                        client.command(f"DROP VIEW {self.warehouse.qualify(name)}")

                await self.warehouse.run(drop)
                await repositories.artifacts.replace(
                    ArtifactLookup(artifact_id=artifact["artifactId"]),
                    {
                        **artifact,
                        "state": "dropped",
                        "droppedAt": utc_now(),
                        "cleanupPending": False,
                    },
                )
            if len(artifacts) < 100:
                return
            after = artifacts[-1]["artifactId"]

    @staticmethod
    async def unreferenced(repositories, artifact):
        if await repositories.bindings.find(
            BindingLookup(artifact_id=artifact["artifactId"])
        ):
            return False
        run = await repositories.runs.require(
            RunLookup(run_id=artifact["runId"]), "Source view build"
        )
        if run["state"] in {"queued", "running", "waiting"}:
            return False
        if await repositories.read_leases.list(
            ReadLeaseLookup(
                artifact_id=artifact["artifactId"], expires_after=utc_now()
            ),
            limit=1,
        ):
            return False
        pins = await repositories.pins.list(
            PinLookup(artifact_id=artifact["artifactId"]), limit=10001
        )
        if len(pins) > 10000:
            return False
        for pin in pins:
            pinned = await repositories.runs.find(RunLookup(run_id=pin["runId"]))
            if pinned and pinned["state"] in {"queued", "running", "waiting"}:
                return False
        return True
