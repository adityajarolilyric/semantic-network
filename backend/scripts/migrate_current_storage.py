"""One-time consolidation of active populations/values. Dry run unless --apply.

Sources are never modified. Active values (including edits) are copied exactly.
A maintenance barrier excludes requests and workers. Old owned tables are removed
only after every active binding has been migrated and verified. Rerunnable.
"""

import argparse
import asyncio
from contextlib import suppress
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.core.config import Settings  # noqa: E402
from backend.app.container import ServiceContainer  # noqa: E402
from backend.app.engine.incremental import MaterialisationManifest  # noqa: E402
from backend.app.engine.captured_inputs import CapturedRunInputs  # noqa: E402
from backend.app.core.metadata import utc_now  # noqa: E402
from backend.app.engine.grain_contexts import resolve_grain_contexts  # noqa: E402
from backend.app.engine.storage_layout import time_key_ids  # noqa: E402
from backend.app.repositories.metadata import metadata_repositories  # noqa: E402
from backend.app.repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ObjectLookup,
    RunLookup,
    TableResourceLookup,
    WorkspaceControlLookup,
)  # noqa: E402
from backend.app.repositories.mongo import MongoMetadataStore  # noqa: E402
from backend.app.services.workspace_gate import WorkspaceGate  # noqa: E402
from backend.app.warehouse.current_storage import STORAGE_POLICY, TableStorage  # noqa: E402
from backend.app.warehouse.repository import ClickHouseWarehouseRepository  # noqa: E402
from backend.app.warehouse.sql import quote_identifier as q, quote_literal as lit  # noqa: E402
from backend.app.warehouse.versioned_storage import relation_sql  # noqa: E402


async def migrate(store, warehouse, *, apply=False):
    repositories = metadata_repositories(store.repository)
    active = await repositories.runs.list(
        RunLookup(state_in=("queued", "running", "waiting")), limit=1
    )
    if active:
        raise RuntimeError("Wait for existing builds to finish before migrating.")
    objects = await repositories.objects.list(
        ObjectLookup(model_id="default_model", revision_id="revision_1"), limit=10000
    )
    container = ServiceContainer(store, warehouse.settings, warehouse)
    graph = resolve_grain_contexts(
        await container.dimensions.graph_objects(
            store.repository, "default_model", "revision_1"
        )
    )
    by_id = {o["objectId"]: o for o in graph}
    bindings = await repositories.bindings.list(
        BindingLookup(model_id="default_model", revision_id="revision_1"), limit=10000
    )
    grouped = {}
    for binding in bindings:
        if binding.get("resourceKind") not in ("dimension", "measureValues"):
            continue
        artifact = await repositories.artifacts.require(
            ArtifactLookup(artifact_id=binding["artifactId"]), "Active artifact"
        )
        grouped.setdefault(binding["resourceId"], []).append(artifact)
    prior_artifacts = await repositories.artifacts.list(
        ArtifactLookup(model_id="default_model", revision_id="revision_1"), limit=100000
    )
    storage = TableStorage(store, warehouse)
    retained = set()
    for dimension_id, artifacts in grouped.items():
        chosen = next(
            (a for a in artifacts if a["resourceKind"] == "measureValues"), artifacts[0]
        )
        dimension = by_id.get(dimension_id)
        if not dimension:
            raise RuntimeError("Active output grain is missing: " + dimension_id)
        virtual_only = (
            chosen["resourceKind"] == "dimension"
            and dimension["definition"]["population"]["operation"] == "source"
        )
        if virtual_only:
            print("Use source view:", dimension["name"])
            if apply:
                await repositories.bindings.delete(
                    BindingLookup(
                        model_id=chosen["modelId"],
                        revision_id=chosen["revisionId"],
                        resource_kind="dimension",
                        resource_id=dimension_id,
                    )
                )
            continue
        if chosen.get("storageMode") == "current":
            retained.add(chosen["physicalName"])
            continue
        print("Consolidate:", dimension["name"], "from", chosen["physicalName"])
        if not apply:
            continue
        query = relation_sql(warehouse, chosen)
        columns = [{**c, "storageName": c["name"]} for c in chosen["columns"]]
        current = await store.transaction(
            lambda repo: storage.reserve(
                repo, chosen, dimension["name"], migration=True
            )
        )
        keys = dimension["definition"]["grain"]["keyIds"]
        current["timeKeyIds"] = list(time_key_ids(dimension, graph))
        order = await storage.layout(
            current,
            columns,
            keys,
            query,
            {"max_threads": 4, "max_memory_usage": 1024**3},
        )
        names = {c["columnId"]: c["name"] for c in columns}

        def copy(connect):
            client = connect()
            target = warehouse.qualify(current["physicalName"])
            info = warehouse.table_info(client, current["physicalName"])
            if info and info[1] != current["storageOwner"]:
                raise RuntimeError("Migration destination ownership changed.")
            selected = ", ".join(q(c["name"]) for c in columns)
            fingerprint = (
                "count(), groupBitXor(cityHash64(toJSONString(tuple("
                + selected
                + "))))"
            )
            before = client.query(f"SELECT {fingerprint} FROM ({query})").first_row
            if not info:
                declarations = ", ".join(
                    q(c["name"]) + " Nullable(" + c["physicalType"] + ")"
                    for c in columns
                )
                ordering = ", ".join(q(names[key]) for key in order)
                client.command(
                    f"CREATE TABLE {target} ({declarations}) ENGINE=MergeTree ORDER BY ({ordering}) SETTINGS allow_nullable_key=1 COMMENT {lit(current['storageOwner'])}"
                )
                client.command(
                    f"INSERT INTO {target} ({selected}) {query}",
                    settings={"max_memory_usage": 1024**3},
                )
            after = client.query(f"SELECT {fingerprint} FROM {target}").first_row
            if before != after:
                raise RuntimeError(
                    "Migration checksum mismatch; original data retained."
                )
            client.command(
                f"CREATE OR REPLACE VIEW {warehouse.qualify(current['viewName'])} AS SELECT {selected} FROM {target}"
            )
            client.command(
                f"ALTER TABLE {warehouse.qualify(current['viewName'])} MODIFY COMMENT {lit(current['storageOwner'] + ':view')}"
            )
            return before[0]

        count = await warehouse.run(copy)

        def converted(artifact):
            result = {
                **artifact,
                "physicalName": current["physicalName"],
                "viewName": current["viewName"],
                "liveName": current["physicalName"],
                "liveViewName": current["viewName"],
                "storageMode": "current",
                "storageOwner": current["storageOwner"],
                "sortKeyIds": order,
                "rowCount": count,
                "migratedAt": utc_now(),
                "migrationSource": artifact.get("migrationSource")
                or {
                    "physicalName": artifact["physicalName"],
                    "storageOwner": artifact.get(
                        "storageOwner", "semanticArtifact:" + artifact["artifactId"]
                    ),
                },
            }
            result.pop("generationToken", None)
            result.pop("legacyGeneration", None)
            result["columns"] = [
                {**c, "storageName": names[c["columnId"]]} for c in artifact["columns"]
            ]
            result["keyColumns"] = {key: names[key] for key in keys}
            for field, identifier in (
                ("measureColumns", "measureId"),
                ("coverageColumns", "coverageFor"),
                ("mixedColumns", "mixedFor"),
            ):
                if field in result:
                    result[field] = {
                        c[identifier]: names[c["columnId"]]
                        for c in artifact["columns"]
                        if c.get(identifier)
                    }
            return result

        async def activate(repo):
            for artifact in artifacts:
                await metadata_repositories(repo).artifacts.replace(
                    ArtifactLookup(artifact_id=artifact["artifactId"]),
                    converted(artifact),
                )

        await store.transaction(activate)
        retained.add(current["physicalName"])
    if not apply:
        return
    # Storage identity changed, semantics did not. Refresh supported freshness and
    # evaluation manifests without recomputing values or discarding manual edits.
    population_bindings = await repositories.bindings.list(
        BindingLookup(
            model_id="default_model",
            revision_id="revision_1",
            resource_kind="dimension",
        ),
        limit=10000,
    )
    pending = list(population_bindings)
    while pending:
        deferred = []
        for binding in pending:
            dimension = by_id[binding["resourceId"]]
            try:
                captured = await container.dimension_builds.capture_population_inputs(
                    store.repository, "default_model", "revision_1", dimension, graph
                )
            except Exception:
                deferred.append(binding)
                continue
            await repositories.bindings.replace(
                BindingLookup(
                    model_id="default_model",
                    revision_id="revision_1",
                    resource_kind="dimension",
                    resource_id=dimension["objectId"],
                ),
                {**binding, "inputHash": captured["inputHash"]},
            )
        if len(deferred) == len(pending):
            raise RuntimeError(
                "Cannot refresh migrated population dependencies; original tables retained."
            )
        pending = deferred
    inputs = await container.measure_builds.capture_build_inputs(
        store.repository, "default_model", "revision_1"
    )
    manifests = MaterialisationManifest(
        inputs.objects,
        inputs.populations,
        inputs.managed_artifacts,
        inputs.revision["baseDimensionId"],
    ).all()
    values_bindings = await repositories.bindings.list(
        BindingLookup(
            model_id="default_model",
            revision_id="revision_1",
            resource_kind="measureValues",
        ),
        limit=10000,
    )

    async def baseline(repo):
        current = metadata_repositories(repo)
        for binding in values_bindings:
            artifact = await current.artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Migrated values"
            )
            captured = await CapturedRunInputs.load(repo, artifact["runId"], 100000)
            prior_versions = {o["objectId"]: o["version"] for o in captured.objects}
            if any(
                prior_versions.get(o["objectId"]) != o["version"]
                for o in inputs.objects
                if o["kind"] != "grain"
            ):
                raise RuntimeError(
                    "Definitions changed since this output was built; explicit refresh required, values retained."
                )
            dimension_id = binding["resourceId"]
            await current.artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {
                    **artifact,
                    "materialisationManifest": manifests[dimension_id],
                    "populationArtifactId": inputs.populations[dimension_id][
                        "artifactId"
                    ],
                    "inputHash": inputs.input_hash,
                },
            )
            await current.bindings.replace(
                BindingLookup(
                    model_id="default_model",
                    revision_id="revision_1",
                    resource_kind="measureValues",
                    resource_id=dimension_id,
                ),
                {
                    **binding,
                    "inputHash": inputs.input_hash,
                    "definitionHash": inputs.input_hash,
                },
            )

    await store.transaction(baseline)
    # Drop only explicitly engine-owned tables, excluding every registered raw source.
    raw = {o["definition"]["tableName"] for o in objects if o["kind"] == "source"}
    artifacts = await repositories.artifacts.list(
        ArtifactLookup(model_id="default_model", revision_id="revision_1"), limit=100000
    )
    obsolete = {}
    migration_sources = [
        {**artifact, **artifact["migrationSource"]}
        for artifact in artifacts
        if artifact.get("migrationSource")
    ]
    for artifact in [*prior_artifacts, *artifacts, *migration_sources]:
        if (
            artifact.get("kind") == "table"
            and artifact["physicalName"] not in retained | raw
        ):
            obsolete.setdefault(artifact["physicalName"], []).append(artifact)
    for name, documents in obsolete.items():
        owners = {
            a.get("storageOwner", "semanticArtifact:" + a["artifactId"])
            for a in documents
        }

        def drop(connect):
            client = connect()
            info = warehouse.table_info(client, name)
            if info:
                if info[0] != "MergeTree" or info[1] not in owners:
                    raise RuntimeError(
                        "Refusing to drop table with changed ownership: " + name
                    )
                client.command(f"DROP TABLE {warehouse.qualify(name)} SYNC")

        await warehouse.run(drop)
        for artifact in documents:
            latest = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=artifact["artifactId"]), "Artifact"
            )
            if latest["physicalName"] != name:
                continue
            await repositories.artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {**artifact, "state": "dropped", "droppedAt": utc_now()},
            )
        print("Removed obsolete engine table:", name)
    resources = await repositories.table_resources.list(
        TableResourceLookup(model_id="default_model", revision_id="revision_1"),
        limit=100000,
    )
    for resource in resources:
        if resource.get("storagePolicy") != STORAGE_POLICY:
            await repositories.table_resources.delete(
                TableResourceLookup(
                    model_id=resource["modelId"],
                    revision_id=resource["revisionId"],
                    resource_kind=resource["resourceKind"],
                    resource_id=resource["resourceId"],
                )
            )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    store = MongoMetadataStore(settings)
    warehouse = ClickHouseWarehouseRepository(settings)
    gate = WorkspaceGate(store)
    await store.open()
    owner = uuid4().hex
    heartbeat = None
    try:
        if args.apply:
            await gate.begin_reset(owner)
            heartbeat = asyncio.create_task(gate.renew_reset(owner))
            await gate.drain()
        await migrate(store, warehouse, apply=args.apply)
        if args.apply:

            async def release(repo):
                state = await gate.require_owner(repo, owner)
                await metadata_repositories(repo).workspace_control.replace(
                    WorkspaceControlLookup(control_id="workspace"),
                    {**state, "phase": "ready", "owner": None, "leaseUntil": utc_now()},
                )

            await gate.internal(release)
    finally:
        if heartbeat:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        await warehouse.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
