"""One-time, resumable rename of existing planning/value tables. Never run by the service.

Preview: python backend/scripts/rename_planning_tables.py
Apply:   python backend/scripts/rename_planning_tables.py --apply

Uses backend/.env. Requires all API/engine processes to use WorkspaceGate.
Only RENAME TABLE and reference updates are performed; no tables/data are copied.
"""

import argparse
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import re
import sys
from datetime import timedelta
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.config import Settings  # noqa: E402
from backend.app.core.errors import Conflict  # noqa: E402
from backend.app.engine.sql.contracts import digest  # noqa: E402
from backend.app.warehouse.current_storage import TableStorage
from backend.app.engine.sql.storage import owner
from backend.app.repositories.mongo import MongoMetadataStore  # noqa: E402
from backend.app.services.base import now, scope  # noqa: E402
from backend.app.services.workspace_gate import WorkspaceGate  # noqa: E402
from backend.app.warehouse.repository import ClickHouseWarehouseRepository  # noqa: E402

TASK = "grainTableRename"


def rewrite_references(value, mapping):
    """Rewrite relation addresses, preserving historical SQL and business text."""
    if isinstance(value, dict):
        return {
            k: mapping.get(v, v)
            if k == "physicalName" and isinstance(v, str)
            else rewrite_references(v, mapping)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [rewrite_references(v, mapping) for v in value]
    return value


class DimensionTableRename:
    def __init__(self, store, warehouse, gate, journal_path, target):
        self.store, self.warehouse, self.gate = store, warehouse, gate
        self.path, self.target = Path(journal_path), target
        self.owner = uuid4().hex

    async def documents(self, collection, query, key):
        after = None
        while True:
            rows = await self.store.repository.list(
                collection, query, key=key, after=after, limit=100
            )
            for row in rows:
                yield row
            if len(rows) < 100:
                return
            after = rows[-1][key]

    async def plan(self):
        resources = {}
        async for artifact in self.documents(
            "engine_artifacts",
            {
                "kind": "table",
                "resourceKind": {"$in": ["dimension", "measureValues"]},
            },
            "artifactId",
        ):
            if artifact["state"] == "dropped":
                continue
            entry = resources.setdefault(
                artifact["physicalName"],
                {**TableStorage.identity(artifact), "owners": set()},
            )
            entry["owners"].add(owner(artifact))
        async for resource in self.documents(
            "engine_table_resources", {}, "physicalName"
        ):
            entry = resources.setdefault(
                resource["physicalName"],
                {**TableStorage.identity(resource), "owners": set()},
            )
            entry["owners"].add(resource["storageOwner"])
        entries = []
        for old, resource in sorted(resources.items()):
            if re.fullmatch(r"grain_[A-Za-z0-9_]+_[a-f0-9]{28}", old):
                continue
            level = await self.store.repository.get(
                "semantic_objects",
                {
                    **scope(resource["modelId"], resource["revisionId"]),
                    "objectId": resource["resourceId"],
                    "kind": "dimension",
                },
            )
            new = TableStorage.physical_name(
                level["name"] if level else resource["resourceId"], resource, old
            )

            def inspect(connect):
                client = connect()
                info = self.warehouse.table_info(client, old)
                target = self.warehouse.table_info(client, new)
                if target:
                    raise Conflict(
                        f"Rename target already exists: {new}. No table was replaced."
                    )
                if info and (
                    info[0] != "MergeTree" or info[1] not in resource["owners"]
                ):
                    raise Conflict(f"Table ownership changed: {old}.")
                count = (
                    int(
                        client.command(
                            f"SELECT count() FROM {self.warehouse.qualify(old)}"
                        )
                    )
                    if info
                    else None
                )
                return count

            count = await self.warehouse.run(inspect)
            entries.append(
                {
                    **resource,
                    "owners": sorted(resource["owners"]),
                    "old": old,
                    "new": new,
                    "rowCount": count,
                }
            )
        return entries

    def save(self, journal):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(journal, indent=2) + "\n")
        temporary.replace(self.path)

    async def acquire(self):
        async def operation(repo):
            state = await self.gate.state(repo)
            if state["phase"] != "ready" and (
                state.get("maintenanceTask") != TASK
                or state.get("maintenanceJournal") != str(self.path.resolve())
                or (state.get("owner") and state["leaseUntil"] > now())
            ):
                raise Conflict(
                    "Another maintenance operation is active. Retry after it finishes or its lease expires."
                )
            await repo.replace(
                "workspace_control",
                {"controlId": "workspace"},
                {
                    **state,
                    "phase": "resetting",
                    "epoch": state["epoch"] + 1,
                    "owner": self.owner,
                    "leaseUntil": now() + timedelta(seconds=180),
                    "maintenanceTask": TASK,
                    "maintenanceJournal": str(self.path.resolve()),
                },
            )

        await self.gate.internal(operation)

    async def patch(self, collection, query, document, mapping):
        updated = rewrite_references(document, mapping)
        if updated == document:
            return

        async def operation(repo):
            await self.gate.require_owner(repo, self.owner)
            await repo.replace(collection, query, updated)

        await self.gate.internal(operation)

    async def rewrite_metadata(self, mapping):
        async for document in self.documents("engine_artifacts", {}, "artifactId"):
            await self.patch(
                "engine_artifacts",
                {"artifactId": document["artifactId"]},
                document,
                mapping,
            )
        # Registry names are the cursor: fetch each exact old name instead of
        # paginating over the field we are mutating.
        for old in mapping:
            document = await self.store.repository.get(
                "engine_table_resources", {"physicalName": old}
            )
            if document:
                await self.patch(
                    "engine_table_resources",
                    TableStorage.identity(document),
                    document,
                    mapping,
                )
        async for run in self.documents("engine_runs", {}, "runId"):
            await self.patch("engine_runs", {"runId": run["runId"]}, run, mapping)
            async for snapshot in self.documents(
                "engine_run_inputs", {"runId": run["runId"]}, "inputId"
            ):
                await self.patch(
                    "engine_run_inputs",
                    {"runId": run["runId"], "inputId": snapshot["inputId"]},
                    snapshot,
                    mapping,
                )

    async def apply(self):
        journal = json.loads(self.path.read_text()) if self.path.exists() else None
        if journal and journal["target"] != self.target:
            raise Conflict(
                "This journal belongs to a different database configuration."
            )
        if journal and journal["state"] == "complete":
            state = await self.gate.internal(self.gate.state)
            if state["phase"] == "ready":
                return journal
        await self.acquire()
        heartbeat = asyncio.create_task(self.gate.renew_reset(self.owner))
        try:
            await self.gate.drain()
            if not journal:
                journal = {
                    "target": self.target,
                    "state": "prepared",
                    "entries": await self.plan(),
                }
                self.save(journal)
            for entry in journal["entries"]:
                await self.gate.internal(
                    lambda repo: self.gate.require_owner(repo, self.owner)
                )

                def rename(connect):
                    client = connect()
                    old = self.warehouse.table_info(client, entry["old"])
                    new = self.warehouse.table_info(client, entry["new"])
                    if old and new:
                        raise Conflict(
                            f"Both rename addresses exist: {entry['old']} and {entry['new']}."
                        )
                    info = old or new
                    if info and (
                        info[0] != "MergeTree" or info[1] not in entry["owners"]
                    ):
                        raise Conflict("Table ownership changed during rename.")
                    if not info and entry["rowCount"] is not None:
                        raise Conflict(
                            f"The table is missing at both addresses: {entry['old']}."
                        )
                    if old:
                        client.command(
                            f"RENAME TABLE {self.warehouse.qualify(entry['old'])} TO {self.warehouse.qualify(entry['new'])}"
                        )
                    if info:
                        count = int(
                            client.command(
                                f"SELECT count() FROM {self.warehouse.qualify(entry['new'])}"
                            )
                        )
                        if count != entry["rowCount"]:
                            raise Conflict(
                                "Row count changed during the maintenance rename."
                            )

                await self.warehouse.run(rename)
            mapping = {entry["old"]: entry["new"] for entry in journal["entries"]}
            await self.rewrite_metadata(mapping)
            journal["state"] = "complete"
            self.save(journal)

            async def release(repo):
                state = await self.gate.require_owner(repo, self.owner)
                await repo.replace(
                    "workspace_control",
                    {"controlId": "workspace"},
                    {
                        **state,
                        "phase": "ready",
                        "owner": None,
                        "leaseUntil": now(),
                        "maintenanceTask": None,
                        "maintenanceJournal": None,
                    },
                )

            await self.gate.internal(release)
            return journal
        except BaseException:

            async def release_failed(repo):
                state = await self.gate.state(repo)
                if state.get("owner") == self.owner:
                    await repo.replace(
                        "workspace_control",
                        {"controlId": "workspace"},
                        {**state, "owner": None, "leaseUntil": now()},
                    )

            await self.gate.internal(release_failed)
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    settings = Settings()
    target = digest(
        [
            settings.mongo_uri.get_secret_value(),
            settings.mongo_database,
            settings.clickhouse_host,
            settings.clickhouse_port,
            settings.clickhouse_database,
        ]
    )
    journal_path = (
        args.journal or Path("/tmp") / f"semantic-grain-rename-{target[:16]}.json"
    )
    store = MongoMetadataStore(settings)
    warehouse = ClickHouseWarehouseRepository(settings)
    gate = WorkspaceGate(store)
    await store.open()
    try:
        migration = DimensionTableRename(store, warehouse, gate, journal_path, target)
        if args.apply:
            result = await migration.apply()
            print(
                f"Renamed {len(result['entries'])} table addresses. Journal: {journal_path}"
            )
        else:
            entries = await migration.plan()
            for entry in entries:
                print(f"{entry['old']} -> {entry['new']} ({entry['rowCount']} rows)")
            print(
                f"{len(entries)} table addresses to rename. Apply with --apply. Journal: {journal_path}"
            )
    finally:
        await warehouse.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
