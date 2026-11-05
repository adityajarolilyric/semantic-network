from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ObjectLookup,
    PinLookup,
    ReadLeaseLookup,
    RunLookup,
    TableResourceLookup,
)
from datetime import timedelta

from ..core.errors import MetadataError
from ..core.metadata import utc_now as now
from ..warehouse.repository import quote_identifier as q
from ..warehouse.repository import quote_literal as lit
from ..engine.table_storage import GENERATION, owner, token


class ArtifactCollector:
    """Retire only owned, unbound artifacts of terminal runs after a 24-hour grace."""

    def __init__(self, service):
        self.service = service
        self.cursor = None
        self.resource_cursor = None

    async def collect(self):
        repo = self.service.store.repository
        candidates = await metadata_repositories(repo).artifacts.list(
            ArtifactLookup(
                created_before=now() - timedelta(hours=24),
                state_in=["candidate", "ready", "retiring"],
            ),
            order_by="artifactId",
            after=self.cursor,
            limit=100,
        )
        self.cursor = candidates[-1]["artifactId"] if len(candidates) == 100 else None
        for artifact in candidates:
            if artifact["state"] == "dropped":
                continue

            async def retire(repo):
                current = await metadata_repositories(repo).artifacts.find(
                    ArtifactLookup(artifact_id=artifact["artifactId"])
                )
                run = await metadata_repositories(repo).runs.find(
                    RunLookup(run_id=current["runId"])
                )
                if run["state"] in ("queued", "running") or await metadata_repositories(
                    repo
                ).bindings.find(BindingLookup(artifact_id=current["artifactId"])):
                    return False
                reads = await metadata_repositories(repo).read_leases.list(
                    ReadLeaseLookup(
                        artifact_id=current["artifactId"], expires_after=now()
                    ),
                    order_by="readId",
                    limit=1,
                )
                if reads:
                    return False
                pins = await metadata_repositories(repo).pins.list(
                    PinLookup(artifact_id=current["artifactId"]),
                    order_by="runId",
                    limit=10001,
                )
                if len(pins) > 10000:
                    return False
                for pin in pins:
                    pinned_run = await metadata_repositories(repo).runs.find(
                        RunLookup(run_id=pin["runId"])
                    )
                    if pinned_run and pinned_run["state"] in ("queued", "running"):
                        return False
                await metadata_repositories(repo).artifacts.replace(
                    ArtifactLookup(artifact_id=current["artifactId"]),
                    {**current, "state": "retiring"},
                )
                return current

            artifact = await self.service.store.transaction(retire)
            if not artifact:
                continue
            warehouse = self.service.executor.warehouse

            def drop(connect):
                client = connect()
                info = warehouse.table_info(client, artifact["physicalName"])
                if info:
                    if info[0] != (
                        "MergeTree" if artifact.get("kind") == "table" else "View"
                    ) or info[1] != owner(artifact):
                        raise MetadataError(
                            "artifact_ownership",
                            "Refusing to remove a view whose ownership changed.",
                        )
                    if artifact.get("storageMode") == "versioned":
                        client.command(
                            f"ALTER TABLE {warehouse.qualify(artifact['physicalName'])} ADD COLUMN IF NOT EXISTS {q(GENERATION)} String DEFAULT {lit(artifact.get('legacyGeneration') or token(artifact))}"
                        )
                        client.command(
                            f"ALTER TABLE {warehouse.qualify(artifact['physicalName'])} DELETE WHERE {q(GENERATION)}={lit(token(artifact))} SETTINGS mutations_sync=1"
                        )
                        return
                    client.command(
                        f"DROP {'TABLE' if artifact.get('kind') == 'table' else 'VIEW'} IF EXISTS {warehouse.qualify(artifact['physicalName'])}"
                    )

            await warehouse.run(drop)
            current = await metadata_repositories(repo).artifacts.find(
                ArtifactLookup(artifact_id=artifact["artifactId"])
            )
            await metadata_repositories(repo).artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"], state="retiring"),
                {**current, "state": "dropped", "droppedAt": now()},
            )

        await self.collect_resources()

    async def collect_resources(self):
        """Drop the pair only after its planning level and all retained rows retire."""

        repo = self.service.store.repository
        rows = await metadata_repositories(repo).table_resources.list(
            TableResourceLookup(),
            order_by="physicalName",
            after=self.resource_cursor,
            limit=100,
        )
        self.resource_cursor = rows[-1]["physicalName"] if len(rows) == 100 else None
        for resource in rows:

            async def retire(repo):
                current = await metadata_repositories(repo).table_resources.find(
                    TableResourceLookup(
                        model_id=resource["modelId"],
                        revision_id=resource["revisionId"],
                        resource_kind=resource["resourceKind"],
                        resource_id=resource["resourceId"],
                    )
                )
                if not current or current["physicalName"] != resource["physicalName"]:
                    return False
                if current.get("state") == "retiring":
                    return True
                level = await metadata_repositories(repo).objects.find(
                    ObjectLookup(
                        model_id=current["modelId"],
                        revision_id=current["revisionId"],
                        object_id=current["resourceId"],
                        kind="planningLevel",
                    )
                )
                retained = await metadata_repositories(repo).artifacts.list(
                    ArtifactLookup(
                        model_id=resource["modelId"],
                        revision_id=resource["revisionId"],
                        resource_kind=resource["resourceKind"],
                        resource_id=resource["resourceId"],
                        state_in=["candidate", "ready", "retiring"],
                    ),
                    order_by="artifactId",
                    limit=1,
                )
                if (
                    level
                    or retained
                    or await metadata_repositories(repo).bindings.find(
                        BindingLookup(
                            model_id=resource["modelId"],
                            revision_id=resource["revisionId"],
                            resource_kind=resource["resourceKind"],
                            resource_id=resource["resourceId"],
                        )
                    )
                ):
                    return False
                await metadata_repositories(repo).table_resources.replace(
                    TableResourceLookup(
                        model_id=resource["modelId"],
                        revision_id=resource["revisionId"],
                        resource_kind=resource["resourceKind"],
                        resource_id=resource["resourceId"],
                    ),
                    {**current, "state": "retiring"},
                )
                return True

            if not await self.service.store.transaction(retire):
                await self.prune_columns(resource)
                continue
            warehouse = self.service.executor.warehouse

            def drop(connect):
                client = connect()
                info = warehouse.table_info(client, resource["physicalName"])
                if info:
                    if info[0] != "MergeTree" or info[1] != resource["storageOwner"]:
                        raise MetadataError(
                            "artifact_ownership",
                            "Refusing to remove a planning table whose ownership changed.",
                        )
                    client.command(
                        f"DROP TABLE {warehouse.qualify(resource['physicalName'])} SYNC"
                    )

            await warehouse.run(drop)
            await metadata_repositories(repo).table_resources.delete(
                TableResourceLookup(
                    model_id=resource["modelId"],
                    revision_id=resource["revisionId"],
                    resource_kind=resource["resourceKind"],
                    resource_id=resource["resourceId"],
                    state="retiring",
                    physical_name=resource["physicalName"],
                )
            )

    async def prune_columns(self, resource):
        """Retire unused storage slots before DDL so overlapping builds cannot reuse them."""

        async def plan(repo):
            current = await metadata_repositories(repo).table_resources.find(
                TableResourceLookup(
                    model_id=resource["modelId"],
                    revision_id=resource["revisionId"],
                    resource_kind=resource["resourceKind"],
                    resource_id=resource["resourceId"],
                )
            )
            if (
                not current
                or current["physicalName"] != resource["physicalName"]
                or current.get("state") == "retiring"
            ):
                return None
            retained = await metadata_repositories(repo).artifacts.list(
                ArtifactLookup(
                    model_id=resource["modelId"],
                    revision_id=resource["revisionId"],
                    resource_kind=resource["resourceKind"],
                    resource_id=resource["resourceId"],
                    state_in=["candidate", "ready", "retiring"],
                ),
                order_by="artifactId",
                limit=10001,
            )
            if len(retained) > 10000:
                return None
            needed = {
                c.get("storageName", c["name"])
                for a in retained
                for c in a.get("columns", [])
            }
            needed.update(current.get("protectedColumns", []))
            removed = {v["name"] for v in current["columns"].values()} - needed
            pending = set(current.get("pendingDrops", [])) | removed
            if not pending:
                return None
            current.update(
                columns={
                    k: v
                    for k, v in current["columns"].items()
                    if v["name"] not in removed
                },
                retiredNames=sorted(set(current.get("retiredNames", [])) | removed),
                pendingDrops=sorted(pending),
            )
            # prepare() writes this same registry document and its artifact schema in
            # one transaction. A concurrent allocation therefore retries against our
            # retired names, never writing a column scheduled for removal.
            await metadata_repositories(repo).table_resources.replace(
                TableResourceLookup(
                    model_id=resource["modelId"],
                    revision_id=resource["revisionId"],
                    resource_kind=resource["resourceKind"],
                    resource_id=resource["resourceId"],
                ),
                current,
            )
            return current

        planned = await self.service.store.transaction(plan)
        if not planned:
            return
        warehouse = self.service.executor.warehouse

        def drop(connect):
            client = connect()
            info = warehouse.table_info(client, planned["physicalName"])
            if info:
                if info[0] != "MergeTree" or info[1] != planned["storageOwner"]:
                    raise MetadataError(
                        "artifact_ownership",
                        "Refusing to alter a planning table whose ownership changed.",
                    )
                for name in planned["pendingDrops"]:
                    client.command(
                        f"ALTER TABLE {warehouse.qualify(planned['physicalName'])} DROP COLUMN IF EXISTS {q(name)}"
                    )

        await warehouse.run(drop)

        async def finish(repo):
            current = await metadata_repositories(repo).table_resources.find(
                TableResourceLookup(
                    model_id=resource["modelId"],
                    revision_id=resource["revisionId"],
                    resource_kind=resource["resourceKind"],
                    resource_id=resource["resourceId"],
                )
            )
            if current and current["physicalName"] == planned["physicalName"]:
                current["pendingDrops"] = sorted(
                    set(current.get("pendingDrops", [])) - set(planned["pendingDrops"])
                )
                await metadata_repositories(repo).table_resources.replace(
                    TableResourceLookup(
                        model_id=resource["modelId"],
                        revision_id=resource["revisionId"],
                        resource_kind=resource["resourceKind"],
                        resource_id=resource["resourceId"],
                    ),
                    current,
                )

        await self.service.store.transaction(finish)
