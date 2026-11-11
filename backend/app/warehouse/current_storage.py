"""One current physical population, with temporary build copies and stable views."""

import logging
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..core.metadata import utc_now
from ..engine.sql.contracts import digest
from ..engine.execution.artifacts.recovery import ArtifactRecoveryLease
from ..engine.execution.storage.layout import sorting_key
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    BindingLookup,
    ArtifactLookup,
    RunLookup,
    TableResourceLookup,
)
from .measure_columns import lookup_name, result_name
from .source_values import SourceValueStorage
from .sql import quote_identifier as q, quote_literal as lit
from .versioned_storage import TableStorage as LegacyTableStorage
from ..engine.sql.storage import owner
from ..engine.execution.network_gate import NetworkBuildGate

STORAGE_POLICY = "single-current-table-exchange-v1"


class TableStorage(LegacyTableStorage):
    """Application-scoped coordinator; all warehouse I/O stays in this package."""

    def __init__(self, store, warehouse):
        super().__init__(store, warehouse)
        self.gate = NetworkBuildGate(store)
        self.recovery_lease = ArtifactRecoveryLease(store)
        self.source_values = SourceValueStorage(self)

    @staticmethod
    def lookup(artifact):
        return TableResourceLookup(
            model_id=artifact["modelId"],
            revision_id=artifact["revisionId"],
            resource_kind="dimension",
            resource_id=artifact["resourceId"],
        )

    async def reserve(self, repo, artifact, level_name, *, migration=False):
        repositories = metadata_repositories(repo)
        lookup = self.lookup(artifact)
        resource = await repositories.table_resources.find(lookup)
        if not migration:
            bindings = await repositories.bindings.list(
                BindingLookup(
                    model_id=artifact["modelId"],
                    revision_id=artifact["revisionId"],
                    resource_id=artifact["resourceId"],
                ),
                limit=10,
            )
            for binding in bindings:
                previous = await repositories.artifacts.require(
                    ArtifactLookup(artifact_id=binding["artifactId"]), "Existing table"
                )
                if (
                    previous.get("kind") == "table"
                    and previous.get("storageMode") == "versioned"
                ):
                    raise MetadataError(
                        "storage_migration_required",
                        "This workspace requires the one-time current-storage migration before rebuilding. Existing values are unchanged.",
                        409,
                    )

        if resource and resource.get("state") == "retiring":
            raise Conflict("This dimension table is being collected. Retry shortly.")
        if not resource or resource.get("storagePolicy") != STORAGE_POLICY:
            identity = {k: artifact[k] for k in ("modelId", "revisionId", "resourceId")}
            identity["resourceKind"] = "dimension"
            name = (
                "grain_"
                + "".join(c if c.isalnum() or c == "_" else "_" for c in level_name)[
                    :80
                ]
                + "_"
                + digest([identity, artifact["artifactId"]])[:16]
            )
            resource = {
                **identity,
                "physicalName": name,
                "viewName": name + "_view",
                "storageOwner": "semanticCurrent:" + digest(identity),
                "storagePolicy": STORAGE_POLICY,
                "sortKeyIds": None,
                "columns": {},
            }
            if await repositories.table_resources.find(lookup):
                await repositories.table_resources.replace(lookup, resource)
            else:
                await repositories.table_resources.insert(resource)
        return {
            **artifact,
            "physicalName": resource["physicalName"],
            "viewName": resource["viewName"],
            "storageOwner": resource["storageOwner"],
            "storageMode": "current",
            "liveName": resource["physicalName"],
            "liveViewName": resource["viewName"],
        }

    async def prepare(self, artifact, compiled, settings):
        def inspect(connect):
            result = connect().query(
                f"SELECT * FROM ({compiled.sql}) LIMIT 0", settings=settings
            )
            return [t.name for t in result.column_types]

        types = await self.warehouse.run(inspect)
        previous = {
            c["columnId"]: c.get("storageName", c["name"])
            for c in (artifact.get("sourceSnapshot") or {}).get("columns", [])
        }
        selected_ids = {c["columnId"] for c in compiled.schema.columns}
        retained = {key: name for key, name in previous.items() if key in selected_ids}
        used = set(retained.values())
        columns = []
        for column, dtype in zip(compiled.schema.columns, types):
            storage_name = retained.get(column["columnId"], column["name"])
            if column["columnId"] not in retained:
                suffix = 0
                while storage_name in used:
                    storage_name = (
                        column["name"]
                        + "__"
                        + digest([column["columnId"], suffix])[:12]
                    )
                    suffix += 1
            used.add(storage_name)
            nullable = dtype.startswith("Nullable(")
            columns.append(
                {
                    **column,
                    "nullable": nullable,
                    "physicalType": dtype[9:-1] if nullable else dtype,
                    "storageName": storage_name,
                }
            )
        if len({c["storageName"] for c in columns}) != len(columns):
            raise Conflict("Materialised output column names must be unique.")
        return columns

    async def ensure_inputs(self, artifacts):
        # Legacy inputs stay readable during the one-time storage migration. Never
        # add an engine column to an imported source or managed view.
        return None

    async def layout(self, artifact, columns, keys, population_query, settings):
        repositories = metadata_repositories(self.store.repository)
        resource = await repositories.table_resources.require(
            self.lookup(artifact), "Dimension storage"
        )
        if resource.get("sortKeyIds") and set(resource["sortKeyIds"]) == set(keys):
            return resource["sortKeyIds"]
        names = {c["columnId"]: c["name"] for c in columns}

        def count(connect):
            expressions = ", ".join(f"uniqExact({q(names[key])})" for key in keys)
            return (
                connect()
                .query(
                    f"SELECT {expressions} FROM ({population_query})", settings=settings
                )
                .first_row
            )

        counts = await self.warehouse.run(count)
        cardinalities = dict(zip(keys, counts))
        order = sorting_key(
            list(keys), cardinalities, set(artifact.get("timeKeyIds", []))
        )
        resource.update(
            sortKeyIds=order, initialCardinalities=cardinalities, sortChosenAt=utc_now()
        )
        await repositories.table_resources.replace(self.lookup(artifact), resource)
        return order

    async def begin(
        self, artifact, columns, keys, population_query, settings, *, preserve=False
    ):
        """Allocate an owned candidate. Never write to the current table here."""
        await self.require_running(artifact)
        order = await self.layout(artifact, columns, keys, population_query, settings)
        candidate = artifact["liveName"] + "_build_" + uuid4().hex[:16]
        marker = owner(artifact) + ":candidate:" + artifact["artifactId"]
        source = artifact.get("sourceSnapshot")
        # A recovered Dimension attempt may inherit the previous candidate's
        # journal. Its original artifact owns recovery; this new attempt must
        # start a fresh candidate lifecycle.
        artifact.pop("publication", None)
        artifact.pop("generationToken", None)
        artifact["cleanupPending"] = True
        artifact.update(
            physicalName=candidate,
            viewName=candidate + "_view",
            buildOwner=marker,
            sortKeyIds=order,
            columns=columns,
        )
        await metadata_repositories(self.store.repository).artifacts.replace(
            ArtifactLookup(artifact_id=artifact["artifactId"]), artifact
        )

        def create(connect):
            client = connect()
            engine = client.command(
                "SELECT engine FROM system.databases WHERE name={db:String}",
                parameters={"db": self.warehouse.settings.clickhouse_database},
            )
            if engine != "Atomic":
                raise MetadataError(
                    "unsupported_database_engine",
                    "Safe table exchange requires an Atomic ClickHouse database.",
                    409,
                )
            table = self.warehouse.qualify(candidate)
            names = {c["columnId"]: c["storageName"] for c in columns}
            live = self.warehouse.table_info(client, artifact["liveName"])
            current = live and live[0] == "MergeTree" and live[1] == owner(artifact)
            if live and not current:
                raise Conflict("The materialised table ownership changed.")
            # Clone only when population, schema and ordering permit preserving data.
            can_clone = (
                preserve
                and source
                and source.get("storageMode") == "current"
                and current
                and source["physicalName"] == artifact["liveName"]
                and source.get("sortKeyIds") == order
            )
            if preserve and not can_clone:
                raise Conflict(
                    "Cannot preserve the existing materialised population for an incremental build. Run Full Semantic Refresh explicitly."
                )
            if can_clone:
                client.command(
                    f"CREATE TABLE {table} CLONE AS {self.warehouse.qualify(artifact['liveName'])}"
                )
                client.command(f"ALTER TABLE {table} MODIFY COMMENT {lit(marker)}")
                wanted = {c["storageName"] for c in columns}
                existing = {
                    c.name: c.type for c in self.warehouse.columns(client, candidate)
                }
                for name in existing.keys() - wanted:
                    client.command(f"ALTER TABLE {table} DROP COLUMN {q(name)}")
                for column in columns:
                    dtype = f"Nullable({column['physicalType']})"
                    if column["storageName"] not in existing:
                        client.command(
                            f"ALTER TABLE {table} ADD COLUMN {q(column['storageName'])} {dtype}"
                        )
                    elif existing[column["storageName"]] != dtype:
                        client.command(
                            f"ALTER TABLE {table} DROP COLUMN {q(column['storageName'])}"
                        )
                        client.command(
                            f"ALTER TABLE {table} ADD COLUMN {q(column['storageName'])} {dtype}"
                        )
            else:
                declarations = ", ".join(
                    q(c["storageName"]) + " Nullable(" + c["physicalType"] + ")"
                    for c in columns
                )
                ordering = ", ".join(q(names[key]) for key in order)
                client.command(
                    f"CREATE TABLE {table} ({declarations}) ENGINE=MergeTree ORDER BY ({ordering}) SETTINGS allow_nullable_key=1 COMMENT {lit(marker)}"
                )
                population = [
                    c
                    for c in columns
                    if not (
                        c.get("measureId") or c.get("coverageFor") or c.get("mixedFor")
                    )
                ]
                target = ", ".join(q(c["storageName"]) for c in population)
                selected = ", ".join(q(c["name"]) for c in population)
                if not artifact.get("populateWithValues"):
                    client.command(
                        f"INSERT INTO {table} ({target}) SELECT {selected} FROM ({population_query}) LIMIT {self.warehouse.settings.planning_max_rows + 1}",
                        settings=settings,
                    )
            selected = ", ".join(
                q(c["storageName"]) + " AS " + q(c["name"]) for c in columns
            )
            client.command(
                f"CREATE VIEW {self.warehouse.qualify(artifact['viewName'])} AS SELECT {selected} FROM {table}"
            )
            client.command(
                f"ALTER TABLE {self.warehouse.qualify(artifact['viewName'])} MODIFY COMMENT {lit(marker + ':view')}"
            )
            return bool(can_clone)

        return await self.warehouse.run(create)

    async def publish(self, artifacts):
        """Journal before each DDL. Publication is reversible until Mongo commits."""
        for artifact in artifacts:
            await self.require_running(artifact)
            await self._publish_one(artifact)

    async def _publish_one(self, artifact):
        candidate = artifact["physicalName"]
        live = artifact["liveName"]
        repositories = metadata_repositories(self.store.repository)

        def inspect(connect):
            client = connect()
            return client.query(
                "SELECT name, toString(uuid), comment, as_select FROM system.tables WHERE database={db:String} AND name IN {names:Array(String)}",
                parameters={
                    "db": self.warehouse.settings.clickhouse_database,
                    "names": [live, candidate, artifact["liveViewName"]],
                },
            ).result_rows

        rows = await self.warehouse.run(inspect)
        comments = {r[0]: r[2] for r in rows}
        if comments.get(candidate) != artifact.get("buildOwner"):
            raise Conflict("The build candidate ownership changed before publication.")
        if live in comments and comments[live] != owner(artifact):
            raise Conflict("The live table ownership changed before publication.")
        uuids = {r[0]: r[1] for r in rows}
        if candidate not in uuids:
            raise Conflict("The build candidate is missing before publication.")
        journal = {
            **artifact,
            "publication": {
                "candidateName": candidate,
                "candidateView": artifact["viewName"],
                "candidateUuid": uuids[candidate],
                "oldUuid": uuids.get(live),
                "oldViewSql": next(
                    (r[3] for r in rows if r[0] == artifact["liveViewName"]), None
                ),
                "state": "prepared",
            },
        }
        await repositories.artifacts.replace(
            ArtifactLookup(artifact_id=artifact["artifactId"]), journal
        )
        artifact.update(journal)

        def exchange(connect):
            client = connect()
            existing_view = self.warehouse.table_info(client, artifact["liveViewName"])
            if existing_view and (
                existing_view[0] != "View"
                or existing_view[1] != owner(artifact) + ":view"
            ):
                raise Conflict("The live dimension view ownership changed.")
            if live in uuids:
                client.command(
                    f"EXCHANGE TABLES {self.warehouse.qualify(live)} AND {self.warehouse.qualify(candidate)}"
                )
            else:
                client.command(
                    f"RENAME TABLE {self.warehouse.qualify(candidate)} TO {self.warehouse.qualify(live)}"
                )
            selected = ", ".join(
                q(c["storageName"]) + " AS " + q(c["name"]) for c in artifact["columns"]
            )
            client.command(
                f"CREATE OR REPLACE VIEW {self.warehouse.qualify(artifact['liveViewName'])} AS SELECT {selected} FROM {self.warehouse.qualify(live)}"
            )
            client.command(
                f"ALTER TABLE {self.warehouse.qualify(live)} MODIFY COMMENT {lit(owner(artifact))}"
            )
            client.command(
                f"ALTER TABLE {self.warehouse.qualify(artifact['liveViewName'])} MODIFY COMMENT {lit(owner(artifact) + ':view')}"
            )

        await self.warehouse.run(exchange)
        artifact.update(physicalName=live, viewName=artifact["liveViewName"])
        artifact["publication"]["state"] = "exchanged"
        await repositories.artifacts.replace(
            ArtifactLookup(artifact_id=artifact["artifactId"]), artifact
        )

    async def finish(self, artifacts):
        for artifact in artifacts:
            publication = artifact.get("publication")
            if not publication:
                continue

            def drop(connect):
                client = connect()
                client.command(
                    f"DROP VIEW IF EXISTS {self.warehouse.qualify(publication['candidateView'])}"
                )
                rows = client.query(
                    "SELECT toString(uuid) FROM system.tables WHERE database={db:String} AND name={name:String}",
                    parameters={
                        "db": self.warehouse.settings.clickhouse_database,
                        "name": publication["candidateName"],
                    },
                ).result_rows
                if rows and rows[0][0] == publication.get("oldUuid"):
                    client.command(
                        f"DROP TABLE IF EXISTS {self.warehouse.qualify(publication['candidateName'])} SYNC"
                    )

            await self.warehouse.run(drop)
            artifact["publication"]["state"] = "cleaned"
            artifact["cleanupPending"] = False

            async def record_cleanup(repo):
                artifacts = metadata_repositories(repo).artifacts
                latest = await artifacts.require(
                    ArtifactLookup(artifact_id=artifact["artifactId"]),
                    "Published table",
                )
                await artifacts.replace(
                    ArtifactLookup(artifact_id=artifact["artifactId"]),
                    {
                        **latest,
                        "publication": artifact["publication"],
                        "cleanupPending": False,
                    },
                )

            await self.store.transaction(record_cleanup)

    async def recover(self, model, revision):
        """Undo interrupted exchanges or clean successful ones by recorded UUID."""
        if getattr(self, "object_views", None):
            await self.object_views.recover(model, revision)
        await self.source_values.recover(model, revision)
        repositories = metadata_repositories(self.store.repository)
        after = None
        while True:
            rows = await repositories.artifacts.list(
                ArtifactLookup(
                    model_id=model,
                    revision_id=revision,
                    storage_mode="current",
                    cleanup_pending=True,
                ),
                order_by="artifactId",
                after=after,
                limit=100,
            )
            for artifact in rows:
                if artifact["state"] == "dropped":
                    continue
                if not artifact.get("publication") and "_build_" not in artifact.get(
                    "physicalName", ""
                ):
                    continue
                run = await repositories.runs.require(
                    RunLookup(run_id=artifact["runId"]), "Build"
                )
                if (
                    run["state"] == "succeeded"
                    and not artifact.get("publication")
                    and artifact.get("resourceKind") != "editCells"
                ):
                    continue
                if (
                    run["state"] in ("queued", "running", "waiting")
                    and artifact.get("attempt", 0) >= run["fence"]
                    and run.get("leaseUntil", utc_now()) > utc_now()
                ):
                    continue
                if artifact.get("resourceKind") == "editCells":
                    await self.rollback(artifact)
                    continue
                if run["state"] == "succeeded":
                    await self.finish([artifact])
                    continue
                await self.rollback(artifact)
            if len(rows) < 100:
                return
            after = rows[-1]["artifactId"]

    async def rollback(self, artifact):
        if artifact.get("storageMode") == "source":
            async with self.recovery_lease.hold(artifact["artifactId"]) as current:
                if current:
                    await self.source_values.cleanup(current, rollback=True)
            return
        async with self.recovery_lease.hold(artifact["artifactId"]) as current:
            if current:
                await self._rollback(current)

    async def _rollback(self, artifact):
        publication = artifact.get("publication")
        candidate = (
            publication["candidateName"] if publication else artifact["physicalName"]
        )
        live = artifact["liveName"]

        def undo(connect):
            client = connect()
            rows = client.query(
                "SELECT name, toString(uuid), comment FROM system.tables WHERE database={db:String} AND name IN {names:Array(String)}",
                parameters={
                    "db": self.warehouse.settings.clickhouse_database,
                    "names": [live, candidate],
                },
            ).result_rows
            actual = {r[0]: (r[1], r[2]) for r in rows}
            if (
                publication
                and actual.get(live, (None,))[0] == publication["candidateUuid"]
            ):
                if publication.get("oldUuid"):
                    if actual.get(candidate, (None,))[0] != publication["oldUuid"]:
                        raise Conflict(
                            "Cannot recover an exchanged table whose ownership changed."
                        )
                    client.command(
                        f"EXCHANGE TABLES {self.warehouse.qualify(live)} AND {self.warehouse.qualify(candidate)}"
                    )
                else:
                    client.command(
                        f"RENAME TABLE {self.warehouse.qualify(live)} TO {self.warehouse.qualify(candidate)}"
                    )
            # Drop candidate view before its backing table. Names alone never grant ownership.
            view = (
                publication["candidateView"]
                if publication
                else artifact.get("viewName")
            )
            if view:
                client.command(f"DROP VIEW IF EXISTS {self.warehouse.qualify(view)}")
            for temporary, role in (
                (lookup_name({"physicalName": candidate}), "lookup"),
                (result_name({"physicalName": candidate}), "values"),
            ):
                info = self.warehouse.table_info(client, temporary)
                if info and info[1] == artifact.get("buildOwner", "") + ":" + role:
                    client.command(
                        "KILL MUTATION WHERE database={db:String} AND table={table:String} AND is_done=0 SYNC",
                        parameters={
                            "db": self.warehouse.settings.clickhouse_database,
                            "table": candidate,
                        },
                    )
                    client.command(
                        f"DROP TABLE {self.warehouse.qualify(temporary)} SYNC"
                    )
            current = self.warehouse.table_info(client, candidate)
            if current and (
                current[1] == artifact.get("buildOwner")
                or (
                    publication
                    and actual.get(live, (None,))[0] == publication["candidateUuid"]
                )
            ):
                client.command(f"DROP TABLE {self.warehouse.qualify(candidate)} SYNC")
            if not publication or not publication.get("oldUuid"):
                # A failed first publication must not leave a dangling live view.
                if publication:
                    client.command(
                        f"DROP VIEW IF EXISTS {self.warehouse.qualify(artifact['liveViewName'])}"
                    )
            elif publication:
                previous_view = (
                    publication.get("oldViewSql")
                    or f"SELECT * FROM {self.warehouse.qualify(live)}"
                )
                client.command(
                    f"CREATE OR REPLACE VIEW {self.warehouse.qualify(artifact['liveViewName'])} AS {previous_view}"
                )
                client.command(
                    f"ALTER TABLE {self.warehouse.qualify(artifact['liveViewName'])} MODIFY COMMENT {lit(owner(artifact) + ':view')}"
                )

        await self.warehouse.run(undo)
        await metadata_repositories(self.store.repository).artifacts.replace(
            ArtifactLookup(artifact_id=artifact["artifactId"]),
            {
                **artifact,
                "state": "dropped",
                "droppedAt": utc_now(),
                "publication": None,
                "cleanupPending": False,
            },
        )

    async def activate(self, artifacts, commit):
        """Exchange candidates, commit metadata, then remove replaced copies."""
        candidates = [
            a
            for a in artifacts
            if a.get("storageMode") == "current" and "_build_" in a["physicalName"]
        ]
        source_artifacts = [a for a in artifacts if a.get("storageMode") == "source"]
        if candidates or source_artifacts:
            await self.gate.drain()
        try:
            for artifact in source_artifacts:
                await self.source_values.publish(artifact)
            for artifact in candidates:
                if not artifact.get("previewCommit"):
                    await self.require_running(artifact)
                await self._publish_one(artifact)
            await commit()
        except BaseException:
            for artifact in reversed(source_artifacts):
                await self.rollback(artifact)
            for artifact in reversed(candidates):
                if artifact.get("publication"):
                    await self.rollback(artifact)
            raise
        # Once metadata commits, publication must never be rolled back. Cleanup is
        # idempotent and retried from the durable journal after a process restart.
        try:
            await self.finish(candidates)
            for artifact in source_artifacts:
                await self.source_values.cleanup(artifact)
        except Exception:
            logging.getLogger(__name__).exception("published_clone_cleanup_deferred")
