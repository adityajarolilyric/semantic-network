"""One physical table per revision/level/role, with isolated row generations."""

import re

from ..core.errors import Conflict
from ..services.base import scope, now
from ..warehouse.repository import quote_identifier as q, quote_literal as lit
from .contracts import digest

GENERATION = "__semantic_generation"
STORAGE_POLICY = "stable-row-generations-v1"


def token(artifact):
    return artifact.get("generationToken", artifact["artifactId"])


def owner(artifact):
    return artifact.get("storageOwner", "semanticArtifact:" + artifact["artifactId"])


def column_sql(column):
    expression = q(column.get("storageName", column["name"]))
    return f"assumeNotNull({expression})" if not column["nullable"] else expression


def relation_sql(warehouse, artifact):
    columns = ", ".join(
        column_sql(c) + " AS " + q(c["name"]) for c in artifact["columns"]
    )
    return f"SELECT {columns} FROM {warehouse.qualify(artifact['physicalName'])} WHERE {q(GENERATION)}={lit(token(artifact))}"


class TableStorage:
    def __init__(self, store, warehouse):
        self.store, self.warehouse = store, warehouse

    @staticmethod
    def identity(artifact):
        return {
            **scope(artifact["modelId"], artifact["revisionId"]),
            "resourceKind": artifact["resourceKind"],
            "resourceId": artifact["resourceId"],
        }

    @staticmethod
    def signature(column):
        return digest(
            [
                column["columnId"],
                column.get("role"),
                column["physicalType"],
                column["nullable"],
            ]
        )

    @staticmethod
    def physical_name(level_name, identity, allocation_id):
        # Keep names readable while bounding the identifier and preserving a unique
        # resource incarnation across revisions and deletion/recreation.
        label = (
            re.sub(r"[^A-Za-z0-9_]+", "_", level_name).strip("_")[:80]
            or "planning_level"
        )
        suffix = digest([identity, allocation_id])[:28]
        role = "_mat" if identity["resourceKind"] == "measureValues" else ""
        return f"grain_{label}{role}_{suffix}"

    async def reserve(self, repo, artifact, level_name):
        identity = self.identity(artifact)
        resource = await repo.get("engine_table_resources", identity)
        if not resource:
            binding = await repo.get("engine_artifact_bindings", identity)
            previous = (
                await repo.get(
                    "engine_artifacts", {"artifactId": binding["artifactId"]}
                )
                if binding
                else None
            )
            name = (
                previous["physicalName"]
                if previous
                else self.physical_name(level_name, identity, artifact["artifactId"])
            )
            marker = (
                owner(previous)
                if previous
                else "semanticTable:" + digest([identity, name])
            )
            resource = {
                **identity,
                "physicalName": name,
                "storageOwner": marker,
                "columns": {},
                "retiredNames": [],
                "pendingDrops": [],
                "protectedColumns": [],
            }
            if previous:
                resource["legacyGeneration"] = token(previous)
                resource["protectedColumns"] = [
                    c.get("storageName", c["name"])
                    for c in previous["columns"]
                    if c.get("role") == "key"
                ]
                for column in previous["columns"]:
                    resource["columns"][self.signature(column)] = {
                        "name": column.get("storageName", column["name"]),
                        "type": column["physicalType"],
                    }
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": previous["artifactId"]},
                    {
                        **previous,
                        "storageMode": "versioned",
                        "storageOwner": marker,
                        "generationToken": token(previous),
                    },
                )
            await repo.insert("engine_table_resources", resource)
        if resource.get("state") == "retiring":
            raise Conflict(
                "This deleted planning table is being collected. Retry shortly."
            )
        return {
            **artifact,
            "physicalName": resource["physicalName"],
            "storageOwner": resource["storageOwner"],
            "storageMode": "versioned",
            "generationToken": artifact["artifactId"],
            "legacyGeneration": resource.get("legacyGeneration"),
        }

    async def reconcile_legacy(self, artifact):
        """Remove obsolete pre-upgrade tables before using the permanent resource.

        A pinned or running legacy generation blocks migration rather than allocating
        another physical table. Current bindings and the adopted table are untouched.
        """
        after = None
        while True:
            rows = await self.store.repository.list(
                "engine_artifacts",
                self.identity(artifact),
                key="artifactId",
                after=after,
                limit=100,
            )
            for old in rows:
                if (
                    old["physicalName"] == artifact["physicalName"]
                    or old["state"] == "dropped"
                ):
                    continue

                async def retire(repo):
                    current = await repo.get(
                        "engine_artifacts", {"artifactId": old["artifactId"]}
                    )
                    if current["state"] == "dropped":
                        return None
                    run = await repo.get("engine_runs", {"runId": current["runId"]})
                    bound = await repo.get(
                        "engine_artifact_bindings",
                        {"artifactId": current["artifactId"]},
                    )
                    reads = await repo.list(
                        "engine_read_leases",
                        {
                            "artifactId": current["artifactId"],
                            "expiresAt": {"$gt": now()},
                        },
                        key="readId",
                        limit=1,
                    )
                    if reads:
                        raise Conflict(
                            "A legacy planning table is pinned by a Planning View query. Retry shortly."
                        )
                    pins = await repo.list(
                        "engine_artifact_pins",
                        {"artifactId": current["artifactId"]},
                        key="runId",
                        limit=10001,
                    )
                    if (
                        bound
                        or (run and run["state"] in ("queued", "running"))
                        or len(pins) > 10000
                    ):
                        raise Conflict(
                            "A legacy planning table is still in use. Retry migration after its builds finish."
                        )
                    for pin in pins:
                        pinned = await repo.get("engine_runs", {"runId": pin["runId"]})
                        if pinned and pinned["state"] in ("queued", "running"):
                            raise Conflict(
                                "A legacy planning table is pinned by a build. Retry after that build finishes."
                            )
                    await repo.replace(
                        "engine_artifacts",
                        {"artifactId": current["artifactId"]},
                        {**current, "state": "retiring"},
                    )
                    return current

                retired = await self.store.transaction(retire)
                if retired:

                    def drop(connect):
                        client = connect()
                        info = self.warehouse.table_info(
                            client, retired["physicalName"]
                        )
                        if info:
                            if info[0] != "MergeTree" or info[1] != owner(retired):
                                raise Conflict(
                                    "Refusing to remove a legacy planning table whose ownership changed."
                                )
                            client.command(
                                f"DROP TABLE {self.warehouse.qualify(retired['physicalName'])} SYNC"
                            )

                    await self.warehouse.run(drop)
                    await self.store.repository.replace(
                        "engine_artifacts",
                        {"artifactId": retired["artifactId"], "state": "retiring"},
                        {**retired, "state": "dropped", "droppedAt": now()},
                    )
            if len(rows) < 100:
                break
            after = rows[-1]["artifactId"]

    async def prepare(self, artifact, compiled, settings):
        await self.reconcile_legacy(artifact)

        def header(connect):
            result = connect().query(
                f"SELECT * FROM ({compiled.sql}) LIMIT 0", settings=settings
            )
            return [t.name for t in result.column_types]

        types = await self.warehouse.run(header)

        async def allocate(repo):
            run = await repo.get("engine_runs", {"runId": artifact["runId"]})
            if (
                not run
                or run["state"] != "running"
                or (
                    artifact.get("attempt") is not None
                    and run["fence"] != artifact["attempt"]
                )
            ):
                raise Conflict("The table build was superseded or cancelled.")
            resource = await repo.get("engine_table_resources", self.identity(artifact))
            if resource.get("state") == "retiring":
                raise Conflict("This planning table is being collected.")
            used = {c["name"] for c in resource["columns"].values()} | set(
                resource.get("retiredNames", [])
            )
            columns = []
            for column, typ in zip(compiled.schema.columns, types):
                nullable = typ.startswith("Nullable(")
                base_type = typ[9:-1] if nullable else typ
                column = {**column, "physicalType": base_type, "nullable": nullable}
                signature = self.signature(column)
                if signature not in resource["columns"]:
                    name = column["name"]
                    slot = len(used)
                    while name in used or name == GENERATION:
                        name = column["name"] + "__" + digest([signature, slot])[:16]
                        slot += 1
                    used.add(name)
                    resource["columns"][signature] = {
                        "name": name,
                        "type": "Nullable(" + base_type + ")",
                    }
                columns.append(
                    {**column, "storageName": resource["columns"][signature]["name"]}
                )
            await repo.replace(
                "engine_table_resources", self.identity(artifact), resource
            )
            current = await repo.get(
                "engine_artifacts", {"artifactId": artifact["artifactId"]}
            )
            await repo.replace(
                "engine_artifacts",
                {"artifactId": artifact["artifactId"]},
                {**current, "columns": columns},
            )
            return columns

        return await self.store.transaction(allocate)

    async def ensure_inputs(self, artifacts):
        tables = [a for a in artifacts if a.get("kind") == "table"]
        if not tables:
            return

        def ensure(connect):
            client = connect()
            for artifact in tables:
                info = self.warehouse.table_info(client, artifact["physicalName"])
                if not info or info[0] != "MergeTree" or info[1] != owner(artifact):
                    raise Conflict("A planning input table is missing or changed.")
                client.command(
                    f"ALTER TABLE {self.warehouse.qualify(artifact['physicalName'])} ADD COLUMN IF NOT EXISTS {q(GENERATION)} String DEFAULT {lit(token(artifact))}"
                )

        await self.warehouse.run(ensure)
