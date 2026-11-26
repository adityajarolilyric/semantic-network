"""Owned output views and reversible write-through publication of source tables.

Imported tables never enter the engine table-resource registry. Only owned views,
short-lived source clones and lookup stages can be collected by this adapter.
"""

from uuid import uuid4

from sqlglot import exp, parse_one

from ..core.errors import Conflict, MetadataError
from ..core.metadata import utc_now
from ..engine.sql.storage import owner
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ArtifactLookup, RunLookup
from .sql import quote_literal as lit, quote_identifier as q
from .source_retirement import SourceCopyRetirement
from .source_output_views import SourceOutputViews


class SourceValueStorage:
    def __init__(self, storage):
        self.storage = storage
        self.warehouse = storage.warehouse
        self.store = storage.store
        self.retirement = SourceCopyRetirement(storage)
        self.output_views = SourceOutputViews(self)

    async def save(self, artifact):
        await metadata_repositories(self.store.repository).artifacts.replace(
            ArtifactLookup(artifact_id=artifact["artifactId"]), artifact
        )

    async def has_rows(self, query, settings):
        return await self.warehouse.run(
            lambda connect: bool(
                connect()
                .query(f"SELECT 1 FROM ({query}) LIMIT 1", settings=settings)
                .result_rows
            )
        )

    def source_stamp(self, client, name):
        parameters = {"db": self.warehouse.settings.clickhouse_database, "table": name}
        schema = client.query(
            "SELECT create_table_query FROM system.tables WHERE database={db:String} AND name={table:String}",
            parameters=parameters,
        ).first_row[0]
        parts = list(
            client.query(
                "SELECT sum(rows),max(max_block_number),max(data_version),(SELECT count() FROM system.mutations WHERE database={db:String} AND table={table:String} AND NOT is_done) FROM system.parts WHERE database={db:String} AND table={table:String} AND active",
                parameters=parameters,
            ).first_row
        )
        if parts[3]:
            raise Conflict(
                "The source has unfinished writes. Retry the edit after they finish."
            )
        return [schema, *parts[:3]]

    async def clone(self, artifact):
        await self.storage.require_running(artifact)

        def create(connect):
            client = connect()
            rows = client.query(
                "SELECT toString(uuid),engine,comment FROM system.tables WHERE database={db:String} AND name={table:String}",
                parameters={
                    "db": self.warehouse.settings.clickhouse_database,
                    "table": artifact["sourceTableName"],
                },
            ).result_rows
            if not rows or rows[0][1] != "MergeTree":
                raise MetadataError(
                    "source_storage_engine",
                    "Source-backed editing requires an ordinary MergeTree source table.",
                )
            return (*rows[0], self.source_stamp(client, artifact["sourceTableName"]))

        source_uuid, _, source_owner, source_stamp = await self.warehouse.run(create)
        artifact.update(
            sourceUuid=source_uuid,
            sourceOwner=source_owner,
            sourceStamp=source_stamp,
            sourceCloneName=artifact["physicalName"] + "_build_" + uuid4().hex[:12],
        )
        await self.save(
            artifact
        )  # Journal ownership before creating any physical copy.

        def copy(connect):
            client = connect()
            table = self.warehouse.qualify(artifact["sourceCloneName"])
            client.command(
                f"CREATE TABLE {table} CLONE AS {self.warehouse.qualify(artifact['sourceTableName'])} COMMENT {lit(artifact['buildOwner'])}"
            )

        await self.warehouse.run(copy)
        return artifact["sourceCloneName"]

    @staticmethod
    def view_query(artifact, query):
        return f"SELECT * FROM ({query}) AS {q('__semantic_owner_' + artifact['artifactId'])}"

    def owns_view(self, client, name, expected_owner):
        info = self.warehouse.table_info(client, name)
        if not info or info[0] != "View":
            return False
        if info[1] == expected_owner:
            return True
        if info[1] or not expected_owner.startswith("semanticArtifact:"):
            return False
        query = client.query(
            "SELECT as_select FROM system.tables WHERE database={db:String} AND name={name:String}",
            parameters={
                "db": self.warehouse.settings.clickhouse_database,
                "name": name,
            },
        ).first_row[0]
        marker = "__semantic_owner_" + expected_owner.removeprefix("semanticArtifact:")
        return any(
            alias.name == marker
            for alias in parse_one(query, read="clickhouse").find_all(exp.TableAlias)
        )

    async def create_view(self, artifact, compiled, settings):
        await self.storage.require_running(artifact)

        def create(connect):
            client = connect()
            view = self.warehouse.qualify(artifact["physicalName"])
            if self.warehouse.table_info(client, artifact["physicalName"]):
                raise Conflict("The source output view already exists.")
            client.command(
                f"CREATE VIEW {view} AS {self.view_query(artifact, compiled.sql)}",
                settings=settings,
            )
            client.command(f"ALTER TABLE {view} MODIFY COMMENT {lit(owner(artifact))}")
            types = client.query(
                f"SELECT * FROM {view} LIMIT 0", settings=settings
            ).column_types
            columns = [
                {
                    **column,
                    "physicalType": dtype.name[9:-1]
                    if dtype.name.startswith("Nullable(")
                    else dtype.name,
                    "nullable": dtype.name.startswith("Nullable("),
                    "storageName": column["name"],
                }
                for column, dtype in zip(compiled.schema.columns, types)
            ]
            # Force value evaluation as well as counting keys. COUNT alone can
            # prune casts/formulas and defer a nullable/overflow failure to reads.
            validation = ", ".join(
                f"sum(cityHash64(tuple({q(column['name'])})))" for column in columns
            )
            stats = client.query(
                f"SELECT count(), {validation} FROM {view}", settings=settings
            ).first_row
            count = int(stats[0])
            return {
                "rowCount": count,
                "columns": columns,
                "bytesOnDisk": 0,
                "incompleteMeasures": {},
                "buildTimingsMs": {},
                "writeMode": "source_view",
                "writer": "source_table",
                "updatedColumns": [],
            }

        return await self.warehouse.run(create)

    @staticmethod
    def replace_table(sql, database, previous, target):
        query = parse_one(sql, read="clickhouse")
        for table in query.find_all(exp.Table):
            if table.name == previous and table.db == database:
                table.set("this", exp.to_identifier(target, quoted=True))
        return query.sql(dialect="clickhouse")

    async def publish(self, artifact):
        await self.storage.require_running(artifact)
        if not artifact.get("sourceCloneName"):
            await self.output_views.publish(artifact)
            return

        def inspect(connect):
            client = connect()
            if (
                self.source_stamp(client, artifact["sourceTableName"])
                != artifact["sourceStamp"]
            ):
                raise Conflict(
                    "The source data or schema changed while this edit was being prepared. Retry using current values."
                )
            rows = client.query(
                "SELECT name,toString(uuid),comment,as_select FROM system.tables WHERE database={db:String} AND name IN {names:Array(String)}",
                parameters={
                    "db": self.warehouse.settings.clickhouse_database,
                    "names": [
                        artifact["sourceTableName"],
                        artifact["sourceCloneName"],
                        *(
                            [artifact["managedSourceViewName"]]
                            if artifact.get("managedSourceViewName")
                            else []
                        ),
                    ],
                },
            ).result_rows
            return {row[0]: row[1:] for row in rows}

        rows = await self.warehouse.run(inspect)
        if (
            rows.get(artifact["sourceTableName"], [None])[0] != artifact["sourceUuid"]
            or rows.get(artifact["sourceCloneName"], [None, None])[1]
            != artifact["buildOwner"]
        ):
            raise Conflict("The source table changed during its edit.")
        artifact["sourcePublication"] = {
            "oldUuid": artifact["sourceUuid"],
            "candidateUuid": rows[artifact["sourceCloneName"]][0],
            **(
                {
                    "managedViewSql": rows[artifact["managedSourceViewName"]][2],
                    "managedViewOwner": rows[artifact["managedSourceViewName"]][1],
                }
                if artifact.get("managedSourceViewName")
                else {}
            ),
        }
        await self.save(artifact)
        live_query = artifact.get("sourceLiveQuery") or self.replace_table(
            artifact["sourceQuery"],
            self.warehouse.settings.clickhouse_database,
            artifact["sourceCloneName"],
            artifact["sourceTableName"],
        )

        def exchange(connect):
            client = connect()
            client.command(
                f"EXCHANGE TABLES {self.warehouse.qualify(artifact['sourceTableName'])} AND {self.warehouse.qualify(artifact['sourceCloneName'])}"
            )
            client.command(
                f"ALTER TABLE {self.warehouse.qualify(artifact['sourceTableName'])} MODIFY COMMENT {lit(artifact['sourceOwner'])}"
            )
            self.refresh_managed_view(client, artifact)
            client.command(
                f"CREATE OR REPLACE VIEW {self.warehouse.qualify(artifact['physicalName'])} AS {self.view_query(artifact, live_query)}"
            )
            client.command(
                f"ALTER TABLE {self.warehouse.qualify(artifact['physicalName'])} MODIFY COMMENT {lit(owner(artifact))}"
            )

        await self.warehouse.run(exchange)
        artifact["sourcePublication"]["exchanged"] = True
        artifact["sourceQuery"] = live_query
        await self.save(artifact)
        await self.output_views.publish(artifact)

    def refresh_managed_view(self, client, artifact):
        if not artifact.get("managedSourceViewName"):
            return
        publication = artifact["sourcePublication"]
        table = self.warehouse.qualify(artifact["managedSourceViewName"])
        if not self.owns_view(
            client, artifact["managedSourceViewName"], publication["managedViewOwner"]
        ):
            raise Conflict("The source input view ownership changed during its edit.")
        client.command(
            f"CREATE OR REPLACE VIEW {table} AS {publication['managedViewSql']}"
        )
        client.command(
            f"ALTER TABLE {table} MODIFY COMMENT {lit(publication['managedViewOwner'])}"
        )

    async def cleanup(self, artifact, *, rollback=False):
        if rollback:
            await self.output_views.rollback(artifact)

        def clean(connect):
            client = connect()
            clone = artifact.get("sourceCloneName")
            publication = artifact.get("sourcePublication")
            if clone:
                rows = client.query(
                    "SELECT name,toString(uuid),comment FROM system.tables WHERE database={db:String} AND name IN {names:Array(String)}",
                    parameters={
                        "db": self.warehouse.settings.clickhouse_database,
                        "names": [clone, artifact["sourceTableName"]],
                    },
                ).result_rows
                actual = {row[0]: row[1:] for row in rows}
                exchanged = (
                    publication
                    and actual.get(artifact["sourceTableName"], [None])[0]
                    == publication["candidateUuid"]
                )
                if rollback and exchanged:
                    if actual.get(clone, [None])[0] != publication["oldUuid"]:
                        raise Conflict("The source edit rollback copy has changed.")
                    client.command(
                        f"EXCHANGE TABLES {self.warehouse.qualify(artifact['sourceTableName'])} AND {self.warehouse.qualify(clone)}"
                    )
                    self.refresh_managed_view(client, artifact)
                existing = self.warehouse.table_info(client, clone)
                if existing and (
                    existing[1] == artifact["buildOwner"]
                    or publication
                    and (
                        not rollback
                        and actual.get(clone, [None])[0] == publication["oldUuid"]
                        or rollback
                        and (
                            exchanged
                            or actual.get(clone, [None])[0]
                            == publication["candidateUuid"]
                        )
                    )
                ):
                    client.command(f"DROP TABLE {self.warehouse.qualify(clone)} SYNC")
                lookup = clone + "_lookup"
                info = self.warehouse.table_info(client, lookup)
                if info and info[1] == artifact["buildOwner"] + ":lookup":
                    client.command(f"DROP TABLE {self.warehouse.qualify(lookup)} SYNC")
            if rollback:
                if self.owns_view(client, artifact["physicalName"], owner(artifact)):
                    client.command(
                        f"DROP VIEW {self.warehouse.qualify(artifact['physicalName'])}"
                    )

        await self.warehouse.run(clean)
        if not rollback:
            await self.output_views.finish(artifact)
            await self.retirement.finish(artifact)
        artifact.update(cleanupPending=False)
        if rollback:
            artifact.update(state="dropped", droppedAt=utc_now())
        await self.save(artifact)

    async def recover(self, model, revision):
        repositories = metadata_repositories(self.store.repository)
        after = None
        while True:
            artifacts = await repositories.artifacts.list(
                ArtifactLookup(
                    model_id=model,
                    revision_id=revision,
                    storage_mode="source",
                    cleanup_pending=True,
                ),
                order_by="artifactId",
                after=after,
                limit=100,
            )
            for artifact in artifacts:
                run = await repositories.runs.require(
                    RunLookup(run_id=artifact["runId"]), "Source output build"
                )
                if (
                    run["state"] in {"queued", "running", "waiting"}
                    and artifact.get("attempt", 0) >= run["fence"]
                    and run.get("leaseUntil", utc_now()) > utc_now()
                ):
                    continue
                async with self.storage.recovery_lease.hold(
                    artifact["artifactId"]
                ) as current:
                    if current:
                        await self.cleanup(
                            current, rollback=run["state"] != "succeeded"
                        )
            if len(artifacts) < 100:
                return
            after = artifacts[-1]["artifactId"]
