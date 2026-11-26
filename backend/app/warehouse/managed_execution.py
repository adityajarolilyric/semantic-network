from sqlglot import exp, parse_one

from ..core.errors import MetadataError
from ..engine.sql.diagnostics import CheckDiagnostics
from .query_execution import WarehouseCheckRunner
from ..engine.sql.contracts import digest, Check
from .sql import quote_identifier, quote_literal


class ClickHouseExecutor:
    def __init__(self, warehouse):
        self.warehouse = warehouse
        self.check_runner = WarehouseCheckRunner(warehouse)
        self.check_diagnostics = CheckDiagnostics(
            {
                "duplicate_pivot_cell": "Duplicate pivot cells must be corrected in the source data.",
                "conflicting_retained_values": "Retained columns have conflicting values for the same record keys.",
            },
            "The declared managed key contains duplicates.",
            row_limit=None,
            detail_length=None,
        )

    async def inspect(self, source):
        preview = await self.warehouse.preview(source["definition"]["tableName"], 0, 1)
        actual = [(c.name, c.type) for c in preview.columns]

        def normalized(physical, nullable=False):
            if physical.startswith("LowCardinality("):
                physical = physical[15:-1]
            if physical.startswith("Nullable("):
                physical, nullable = physical[9:-1], True
            return physical, nullable

        expected = [
            (c["name"], normalized(c["physicalType"], c["nullable"]))
            for c in source["definition"]["columns"]
        ]
        comparable = [(name, normalized(typ)) for name, typ in actual]
        if comparable != expected:
            raise MetadataError(
                "source_schema_changed",
                "The raw schema changed. Refresh source registration before building.",
                409,
            )
        return digest(actual)

    async def checks(self, plan):
        await self.check_runner.run(
            plan.checks,
            {"max_execution_time": self.warehouse.settings.clickhouse_timeout_sec},
            self.check_diagnostics,
        )

    @staticmethod
    def owner_alias(artifact):
        return "__semantic_owner_" + artifact["artifactId"]

    def artifact_query(self, artifact, plan):
        # Ownership survives a crash between CREATE and ALTER COMMENT, including
        # ClickHouse's canonical rewrites of CASE, casts and tuple access.
        return f"SELECT * FROM ({plan.sql}) AS {quote_identifier(self.owner_alias(artifact))}"

    async def create(self, artifact, plan):
        if artifact.get("storageMode") == "source_input":
            await self.verify(artifact, plan)
            return

        def operation(connect):
            client = connect()
            name = artifact["physicalName"]
            marker = "semanticArtifact:" + artifact["artifactId"]
            existing = self.warehouse.table_info(client, name)
            if existing:
                owned = existing[0] == "View" and existing[1] == marker
                if existing[0] == "View" and not existing[1]:
                    # Recovery between CREATE VIEW and the ownership-comment DDL.
                    saved = client.query(
                        "SELECT as_select FROM system.tables WHERE database={db:String} AND name={name:String}",
                        parameters={
                            "db": self.warehouse.settings.clickhouse_database,
                            "name": name,
                        },
                    ).first_row[0]

                    owned = any(
                        alias.name == self.owner_alias(artifact)
                        for alias in parse_one(saved, read="clickhouse").find_all(
                            exp.TableAlias
                        )
                    )
                if not owned:
                    raise MetadataError(
                        "artifact_ownership",
                        "Candidate artifact ownership could not be verified.",
                        409,
                    )
            else:
                client.command(
                    f"CREATE VIEW {self.warehouse.qualify(name)} AS {self.artifact_query(artifact, plan)}"
                )
            client.command(
                f"ALTER TABLE {self.warehouse.qualify(name)} MODIFY COMMENT {quote_literal(marker)}"
            )
            header = self.warehouse.columns(client, name)
            if [c.name for c in header] != [c["name"] for c in plan.schema.columns]:
                raise MetadataError(
                    "artifact_schema_mismatch",
                    "Candidate output schema does not match the analyzed schema.",
                )

        await self.warehouse.run(operation)

    async def verify(self, artifact, plan):
        def operation(connect):
            client = connect()
            info = self.warehouse.table_info(client, artifact["physicalName"])
            direct = artifact.get("storageMode") == "source_input"
            valid = (
                (
                    info
                    and info[0] == "MergeTree"
                    and artifact["physicalName"] == artifact.get("sourceTableName")
                )
                if direct
                else (
                    info
                    and info[0] == "View"
                    and info[1] == "semanticArtifact:" + artifact["artifactId"]
                )
            )
            if not valid:
                raise MetadataError(
                    "managed_view_unavailable",
                    "The saved managed view is missing or changed. Use Ensure saved view.",
                    409,
                )
            result = client.query(
                f"SELECT * FROM {self.warehouse.qualify(artifact['physicalName'])} LIMIT 0"
            )
            if list(result.column_names) != [c["name"] for c in plan.schema.columns]:
                raise MetadataError(
                    "managed_view_changed",
                    "The saved managed view schema changed. Rebuild it.",
                    409,
                )

        await self.warehouse.run(operation)

    async def verify_object_view(self, artifact):
        def verify(connect):
            client = connect()
            info = self.warehouse.table_info(client, artifact["physicalName"])
            if (
                not info
                or info[0] != "View"
                or info[1] != "semanticArtifact:" + artifact["artifactId"]
            ):
                raise MetadataError(
                    "object_view_unavailable",
                    "The saved object view is missing or changed. Save the object again.",
                    409,
                )

        await self.warehouse.run(verify)
        await self.check_runner.run(
            tuple(Check(c["code"], c["sql"]) for c in artifact.get("checks", [])),
            {"max_execution_time": self.warehouse.settings.clickhouse_timeout_sec},
            self.check_diagnostics,
        )

    async def preview(self, plan, table=None):
        await self.checks(plan)

        def operation(connect):
            query = (
                f"SELECT * FROM {self.warehouse.qualify(table)}" if table else plan.sql
            )
            client = connect()
            result = client.query(
                f"SELECT * FROM ({query}) ORDER BY ALL LIMIT 50",
                settings={
                    "max_execution_time": self.warehouse.settings.clickhouse_timeout_sec
                },
            )
            return {
                "columns": [dict(c) for c in plan.schema.columns],
                "rows": [
                    [self.warehouse.cell(v) for v in row] for row in result.result_rows
                ],
                "limit": 50,
                "rowCount": int(
                    client.command(
                        f"SELECT count() FROM ({query})",
                        settings={
                            "max_execution_time": self.warehouse.settings.clickhouse_timeout_sec
                        },
                    )
                ),
            }

        return await self.warehouse.run(operation)

    async def attribute_values(self, source, column_id, offset, limit):
        column = next(
            (c for c in source["definition"]["columns"] if c["columnId"] == column_id),
            None,
        )
        if column is None:
            raise MetadataError(
                "unknown_column", "Choose an attribute column from the selected table."
            )

        def operation(connect):
            result = connect().query(
                f"SELECT DISTINCT {quote_identifier(column['name'])} FROM {self.warehouse.qualify(source['definition']['tableName'])} ORDER BY ALL LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}",
                parameters={"limit": limit + 1, "offset": offset},
                settings={
                    "max_execution_time": self.warehouse.settings.clickhouse_timeout_sec
                },
            )
            values = [r[0] for r in result.result_rows]
            # JSON-safe exact literals. Dates/decimals retain textual representation.
            values = [
                str(v)
                if isinstance(v, int) and abs(v) > 9007199254740991
                else v
                if v is None or isinstance(v, (str, int, float, bool))
                else self.warehouse.cell(v)
                for v in values
            ]
            return {
                "values": values[:limit],
                "nextOffset": offset + limit if len(values) > limit else None,
            }

        return await self.warehouse.run(operation)
