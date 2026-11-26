"""Owned, reversible publication and bounded reads of saved object views."""

from sqlglot import exp, parse_one

from ..core.errors import Conflict
from ..engine.sql.storage import owner
from ..engine.sql.contracts import Check, ExecutionPlan
from .sql import quote_identifier as q, quote_literal as lit


class ObjectViewStorage:
    def __init__(self, warehouse, executor):
        self.warehouse, self.executor = warehouse, executor

    async def create(self, artifact, plan):
        await self.executor.checks(plan.execution)

        def create(connect):
            client = connect()
            name = self.warehouse.qualify(artifact["physicalName"])
            marker = q("__semantic_owner_" + artifact["artifactId"])
            client.command(
                f"CREATE VIEW {name} AS SELECT * FROM ({plan.execution.sql}) AS {marker}",
                settings=self.executor.execution_settings(),
            )
            client.command(f"ALTER TABLE {name} MODIFY COMMENT {lit(owner(artifact))}")
            result = client.query(f"SELECT * FROM {name} LIMIT 0")
            types = {
                name: dtype.name
                for name, dtype in zip(result.column_names, result.column_types)
            }
            columns = []
            for column in plan.columns:
                dtype = types[column["storageName"]]
                columns.append(
                    {
                        **column,
                        "nullable": dtype.startswith("Nullable("),
                        "physicalType": dtype[9:-1]
                        if dtype.startswith("Nullable(")
                        else dtype,
                    }
                )
            count = int(
                client.command(
                    f"SELECT count() FROM {name}",
                    settings=self.executor.execution_settings(),
                )
            )
            return columns, count

        return await self.warehouse.run(create)

    async def identities(self, names):
        return await self.warehouse.run(
            lambda connect: dict(
                connect()
                .query(
                    "SELECT name,toString(uuid) FROM system.tables WHERE database={db:String} AND name IN {names:Array(String)}",
                    parameters={
                        "db": self.warehouse.settings.clickhouse_database,
                        "names": names,
                    },
                )
                .result_rows
            )
        )

    async def publish(self, artifact, journal):
        previous = artifact.get("previousView")
        if not previous:
            return
        if not await self.exists(previous):
            raise Conflict("The previous saved view disappeared during Save.")
        candidate, live = artifact["physicalName"], previous["physicalName"]
        ids = await self.identities([candidate, live])
        artifact["publication"] = {
            "candidate": candidate,
            "live": live,
            "oldUuid": ids[live],
            "newUuid": ids[candidate],
        }
        await journal(artifact)
        await self.warehouse.run(
            lambda connect: connect().command(
                f"EXCHANGE TABLES {self.warehouse.qualify(candidate)} AND {self.warehouse.qualify(live)}"
            )
        )
        artifact.update(physicalName=live, viewName=live)
        await journal(artifact)

    async def exists(self, artifact):
        def inspect(connect):
            info = self.warehouse.table_info(connect(), artifact["physicalName"])
            if not info:
                return False
            if info[0] != "View" or info[1] != owner(artifact):
                raise Conflict("The object-view name is owned by another resource.")
            return True

        return await self.warehouse.run(inspect)

    async def verify(self, artifact):
        def verify(connect):
            info = self.warehouse.table_info(connect(), artifact["physicalName"])
            if not info or info[0] != "View" or info[1] != owner(artifact):
                raise Conflict(
                    "The saved object view is missing or its ownership changed. Save the object again."
                )

        await self.warehouse.run(verify)
        await self.executor.checks(
            ExecutionPlan(
                None,
                tuple(Check(c["code"], c["sql"]) for c in artifact.get("checks", [])),
                None,
            )
        )

    async def cleanup(self, artifact, rollback=False):
        publication = artifact.get("publication")
        if publication and rollback:
            ids = await self.identities([publication["candidate"], publication["live"]])
            if ids.get(publication["live"]) == publication["newUuid"]:
                if ids.get(publication["candidate"]) != publication["oldUuid"]:
                    raise Conflict("The object-view rollback copy changed.")
                await self.warehouse.run(
                    lambda connect: connect().command(
                        f"EXCHANGE TABLES {self.warehouse.qualify(publication['candidate'])} AND {self.warehouse.qualify(publication['live'])}"
                    )
                )
            elif ids.get(publication["live"]) != publication["oldUuid"]:
                raise Conflict("The saved view changed during rollback.")
        name = publication["candidate"] if publication else artifact["physicalName"]
        if not rollback and not publication:
            await self.retire_identity_inputs(artifact)
            return
        expected = owner(artifact) if rollback else owner(artifact["previousView"])

        def drop(connect):
            client = connect()
            info = self.warehouse.table_info(client, name)
            if info:
                # Ownership alias covers a crash between CREATE and comment DDL.
                if info[0] != "View" or info[1] not in {expected, ""}:
                    raise Conflict("Object-view cleanup ownership changed.")
                if not info[1]:
                    query = client.query(
                        "SELECT as_select FROM system.tables WHERE database={db:String} AND name={name:String}",
                        parameters={
                            "db": self.warehouse.settings.clickhouse_database,
                            "name": name,
                        },
                    ).first_row[0]
                    if not any(
                        alias.name == "__semantic_owner_" + artifact["artifactId"]
                        for alias in parse_one(query, read="clickhouse").find_all(
                            exp.TableAlias
                        )
                    ):
                        raise Conflict(
                            "Object-view cleanup ownership cannot be verified."
                        )
                client.command(f"DROP VIEW {self.warehouse.qualify(name)}")

        await self.warehouse.run(drop)
        if not rollback:
            await self.retire_identity_inputs(artifact)

    async def retire_identity_inputs(self, artifact):
        def retire(connect):
            client = connect()
            for reference in artifact.get("identityInputs", []):
                previous = reference["retiredInputView"]
                name = previous["physicalName"]
                info = self.warehouse.table_info(client, name)
                if info:
                    if (
                        name == reference["sourceTableName"]
                        or info[0] != "View"
                        or info[1] != owner(previous)
                    ):
                        raise Conflict("The redundant input view ownership changed.")
                    client.command(f"DROP VIEW {self.warehouse.qualify(name)}")

        await self.warehouse.run(retire)

    def read_sql(self, artifact):
        columns = ", ".join(
            q(c["storageName"]) + " AS " + q(c["name"]) for c in artifact["columns"]
        )
        return (
            f"SELECT {columns} FROM {self.warehouse.qualify(artifact['physicalName'])}"
        )

    async def preview(self, artifact):
        await self.verify(artifact)
        query = self.read_sql(artifact)

        def read(connect):
            client = connect()
            rows = client.query(
                query + " ORDER BY ALL LIMIT 50",
                settings=self.executor.execution_settings(),
            ).result_rows
            count = int(
                client.command(
                    "SELECT count() FROM (" + query + ")",
                    settings=self.executor.execution_settings(),
                )
            )
            return {
                "columns": artifact["columns"],
                "rows": [[self.warehouse.cell(v) for v in row] for row in rows],
                "rowCount": count,
                "limit": 50,
                "sql": query,
            }

        return await self.warehouse.run(read)
