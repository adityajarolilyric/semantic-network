from .table_storage import GENERATION, owner, token
from ..warehouse.repository import quote_identifier as q, quote_literal as lit
from datetime import timedelta
from ..core.errors import MetadataError
from ..services.base import now


class ArtifactCollector:
    """Retire only owned, unbound artifacts of terminal runs after a 24-hour grace."""

    def __init__(self, service):
        self.service = service
        self.cursor = None
        self.resource_cursor = None

    async def collect(self):
        repo = self.service.store.repository
        candidates = await repo.list(
            "engine_artifacts",
            {
                "createdAt": {"$lt": now() - timedelta(hours=24)},
                "state": {"$in": ["candidate", "ready", "retiring"]},
            },
            key="artifactId",
            after=self.cursor,
            limit=100,
        )
        self.cursor = candidates[-1]["artifactId"] if len(candidates) == 100 else None
        for artifact in candidates:
            if artifact["state"] == "dropped":
                continue

            async def retire(repo):
                current = await repo.get(
                    "engine_artifacts", {"artifactId": artifact["artifactId"]}
                )
                run = await repo.get("engine_runs", {"runId": current["runId"]})
                if run["state"] in ("queued", "running") or await repo.get(
                    "engine_artifact_bindings", {"artifactId": current["artifactId"]}
                ):
                    return False
                reads = await repo.list(
                    "engine_read_leases",
                    {"artifactId": current["artifactId"], "expiresAt": {"$gt": now()}},
                    key="readId",
                    limit=1,
                )
                if reads:
                    return False
                pins = await repo.list(
                    "engine_artifact_pins",
                    {"artifactId": current["artifactId"]},
                    key="runId",
                    limit=10001,
                )
                if len(pins) > 10000:
                    return False
                for pin in pins:
                    pinned_run = await repo.get("engine_runs", {"runId": pin["runId"]})
                    if pinned_run and pinned_run["state"] in ("queued", "running"):
                        return False
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": current["artifactId"]},
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
            current = await repo.get(
                "engine_artifacts", {"artifactId": artifact["artifactId"]}
            )
            await repo.replace(
                "engine_artifacts",
                {"artifactId": artifact["artifactId"], "state": "retiring"},
                {**current, "state": "dropped", "droppedAt": now()},
            )

        await self.collect_resources()

    async def collect_resources(self):
        """Drop the pair only after its planning level and all retained rows retire."""
        from .table_storage import TableStorage

        repo = self.service.store.repository
        rows = await repo.list(
            "engine_table_resources",
            {},
            key="physicalName",
            after=self.resource_cursor,
            limit=100,
        )
        self.resource_cursor = rows[-1]["physicalName"] if len(rows) == 100 else None
        for resource in rows:
            identity = TableStorage.identity(resource)

            async def retire(repo):
                current = await repo.get("engine_table_resources", identity)
                if not current or current["physicalName"] != resource["physicalName"]:
                    return False
                if current.get("state") == "retiring":
                    return True
                level = await repo.get(
                    "semantic_objects",
                    {
                        "modelId": current["modelId"],
                        "revisionId": current["revisionId"],
                        "objectId": current["resourceId"],
                        "kind": "planningLevel",
                    },
                )
                retained = await repo.list(
                    "engine_artifacts",
                    {**identity, "state": {"$in": ["candidate", "ready", "retiring"]}},
                    key="artifactId",
                    limit=1,
                )
                if (
                    level
                    or retained
                    or await repo.get("engine_artifact_bindings", identity)
                ):
                    return False
                await repo.replace(
                    "engine_table_resources", identity, {**current, "state": "retiring"}
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
            await repo.delete(
                "engine_table_resources",
                {
                    **identity,
                    "state": "retiring",
                    "physicalName": resource["physicalName"],
                },
            )

    async def prune_columns(self, resource):
        """Retire unused storage slots before DDL so overlapping builds cannot reuse them."""
        from .table_storage import TableStorage

        identity = TableStorage.identity(resource)

        async def plan(repo):
            current = await repo.get("engine_table_resources", identity)
            if (
                not current
                or current["physicalName"] != resource["physicalName"]
                or current.get("state") == "retiring"
            ):
                return None
            retained = await repo.list(
                "engine_artifacts",
                {**identity, "state": {"$in": ["candidate", "ready", "retiring"]}},
                key="artifactId",
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
            await repo.replace("engine_table_resources", identity, current)
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
            current = await repo.get("engine_table_resources", identity)
            if current and current["physicalName"] == planned["physicalName"]:
                current["pendingDrops"] = sorted(
                    set(current.get("pendingDrops", [])) - set(planned["pendingDrops"])
                )
                await repo.replace("engine_table_resources", identity, current)

        await self.service.store.transaction(finish)
