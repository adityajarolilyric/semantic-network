from time import perf_counter

from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q
from ..warehouse.repository import quote_literal
from ..engine.checks import (
    PLANNING_CHECK_MESSAGES,
    CheckDiagnostics,
    WarehouseCheckRunner,
)
from ..engine.table_storage import GENERATION, owner, relation_sql


class PlanningTableExecutor:
    def __init__(self, warehouse, settings):
        self.warehouse, self.settings = warehouse, settings
        self.check_runner = WarehouseCheckRunner(warehouse)
        self.check_diagnostics = CheckDiagnostics(
            PLANNING_CHECK_MESSAGES,
            "Dimension mappings must match exactly one member per population row.",
        )

    def execution_settings(self):
        return {
            "max_execution_time": self.settings.clickhouse_timeout_sec,
            "max_threads": self.settings.clickhouse_max_threads,
            "max_memory_usage": self.settings.planning_max_bytes,
            "join_use_nulls": 1,
            "http_write_exception_in_output_format": 0,
            "max_query_size": self.settings.measure_max_sql_bytes,
            "max_expanded_ast_elements": 1000000,
            "short_circuit_function_evaluation": "force_enable",
        }

    async def estimate(self, plan, compiler):
        def operation(connect):
            counts = []
            for scan in plan.scans:
                sql, _ = compiler.render(scan)
                counts.append(
                    int(
                        connect().command(
                            f"SELECT count() FROM ({sql})",
                            settings=self.execution_settings(),
                        )
                    )
                )
            # Count the final public projection, excluding internal lookup flags.
            # Raw Cartesian counts may vastly overstate a coarser selected grain.
            query, _ = compiler.render(plan.root)
            selected = ", ".join(q(c["columnId"]) for c in plan.schema.columns)
            total = int(
                connect().command(
                    f"SELECT count() FROM (SELECT DISTINCT {selected} FROM ({query}) LIMIT {self.settings.planning_max_rows + 1})",
                    settings=self.execution_settings(),
                )
            )
            if total > self.settings.planning_max_rows:
                raise MetadataError(
                    "planning_row_limit",
                    f"Estimated population {total:,} exceeds the configured {self.settings.planning_max_rows:,} row limit.",
                )
            return {
                "factorRowCounts": counts,
                "estimatedRows": total,
                "maxRows": self.settings.planning_max_rows,
                "maxBytes": self.settings.planning_max_bytes,
            }

        return await self.warehouse.run(operation)

    async def checks(self, compiled):
        await self.check_runner.run(
            compiled.checks,
            self.execution_settings(),
            self.check_diagnostics,
        )

    async def build(self, artifact, compiled, plan):
        build_started = perf_counter()
        storage = getattr(self, "storage", None)
        storage_columns = (
            await storage.prepare(artifact, compiled, self.execution_settings())
            if artifact.get("storageMode") == "versioned"
            else None
        )

        schema_prepared = perf_counter()

        def operation(connect):
            operation_started = perf_counter()
            client = connect()
            table = self.warehouse.qualify(artifact["physicalName"])
            marker = owner(artifact)
            existing = self.warehouse.table_info(client, artifact["physicalName"])
            if existing and not storage_columns:
                raise MetadataError(
                    "artifact_ownership",
                    "A candidate table already exists. Retry as a new build.",
                    409,
                )
            # Versioned storage.prepare already resolved authoritative ClickHouse types.
            # Repeating LIMIT 0 expands the entire measure DAG a second time.
            header = None
            if not storage_columns:
                header = client.query(
                    f"SELECT * FROM ({compiled.sql}) LIMIT 0",
                    settings=self.execution_settings(),
                )
            names = {c["columnId"]: c["name"] for c in plan.schema.columns}
            keys = ", ".join(q(names[k]) for k in plan.key_ids)
            if storage_columns:
                from ..warehouse.repository import quote_literal as lit

                if existing and (existing[0] != "MergeTree" or existing[1] != marker):
                    raise MetadataError(
                        "artifact_ownership",
                        "The stable planning table ownership changed.",
                    )
                if not existing:
                    client.command(
                        f"CREATE TABLE {table} ({q(GENERATION)} String) ENGINE=MergeTree ORDER BY ({q(GENERATION)}) COMMENT {lit(marker)}"
                    )
                else:
                    client.command(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {q(GENERATION)} String DEFAULT {lit(artifact.get('legacyGeneration') or artifact['artifactId'])}"
                    )
                for column in storage_columns:
                    client.command(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {q(column['storageName'])} Nullable({column['physicalType']})"
                    )
                target = ", ".join(
                    [q(GENERATION), *(q(c["storageName"]) for c in storage_columns)]
                )
                selected = ", ".join(
                    [
                        lit(artifact["generationToken"]),
                        *(q(c["name"]) for c in storage_columns),
                    ]
                )
                insert_started = perf_counter()
                client.command(
                    f"INSERT INTO {table} ({target}) SELECT {selected} FROM ({compiled.sql}) LIMIT {self.settings.planning_max_rows + 1}",
                    settings=self.execution_settings(),
                )
                table = (
                    "("
                    + relation_sql(
                        self.warehouse, {**artifact, "columns": storage_columns}
                    )
                    + ")"
                )
            else:
                declarations = ", ".join(
                    q(name) + " " + typ.name
                    for name, typ in zip(header.column_names, header.column_types)
                )
                client.command(
                    f"CREATE TABLE {table} ({declarations}) ENGINE = MergeTree ORDER BY ({keys}) SETTINGS allow_nullable_key=1 COMMENT {quote_literal(marker)}"
                )
                insert_started = perf_counter()
                client.command(
                    f"INSERT INTO {table} SELECT * FROM ({compiled.sql}) LIMIT {self.settings.planning_max_rows + 1}",
                    settings=self.execution_settings(),
                )
            insert_finished = perf_counter()
            count = int(
                client.command(
                    f"SELECT count() FROM {table}", settings=self.execution_settings()
                )
            )
            if count > self.settings.planning_max_rows:
                raise MetadataError(
                    "planning_row_limit",
                    "Population grew beyond the configured row limit during the build.",
                )
            nulls = " OR ".join(f"isNull({q(names[k])})" for k in plan.key_ids)
            if client.query(
                f"SELECT {keys} FROM {table} WHERE {nulls} LIMIT 20",
                settings=self.execution_settings(),
            ).result_rows:
                raise MetadataError(
                    "null_planning_key", "Planning grain keys cannot be NULL."
                )
            rows = client.query(
                f"SELECT {keys}, count() FROM {table} GROUP BY {keys} HAVING count() > 1 LIMIT 20",
                settings=self.execution_settings(),
            ).result_rows
            if rows:
                raise MetadataError(
                    "duplicate_planning_key",
                    "The same planning grain has conflicting output attribute values. Select attributes determined by the grain or correct the source data.",
                    details=[
                        {"message": str([self.warehouse.cell(v) for v in row])}
                        for row in rows
                    ],
                )
            size = int(
                client.command(
                    "SELECT coalesce(sum(bytes_on_disk),0) FROM system.parts WHERE database={db:String} AND table={table:String} AND active",
                    parameters={
                        "db": self.settings.clickhouse_database,
                        "table": artifact["physicalName"],
                    },
                )
            )
            if size > self.settings.planning_max_bytes:
                raise MetadataError(
                    "planning_byte_limit",
                    "Planning output exceeds the configured storage limit.",
                )
            coverage_columns = [
                (i, c)
                for i, c in enumerate(plan.schema.columns)
                if c.get("coverageFor")
            ]
            coverage_counts = {}
            if coverage_columns:
                expressions = ", ".join(
                    f"countIf({q(c['name'])}=0)" for i, c in coverage_columns
                )
                counts = client.query(
                    f"SELECT {expressions} FROM {table}",
                    settings=self.execution_settings(),
                ).first_row
                coverage_counts = {
                    c["coverageFor"]: int(n)
                    for (_, c), n in zip(coverage_columns, counts)
                    if n
                }
            return {
                "rowCount": count,
                "incompleteMeasures": coverage_counts,
                "bytesOnDisk": size,
                "buildTimingsMs": {
                    "prepareSchema": round((schema_prepared - build_started) * 1000, 3),
                    "tableSetup": round((insert_started - operation_started) * 1000, 3),
                    "insert": round((insert_finished - insert_started) * 1000, 3),
                    "verifyStoredTable": round(
                        (perf_counter() - insert_finished) * 1000, 3
                    ),
                    "total": round((perf_counter() - build_started) * 1000, 3),
                },
                "columns": storage_columns
                if storage_columns
                else [
                    {
                        **c,
                        "physicalType": typ.name.removeprefix("Nullable(").removesuffix(
                            ")"
                        )
                        if typ.name.startswith("Nullable(")
                        else typ.name,
                        "nullable": typ.name.startswith("Nullable("),
                    }
                    for c, typ in zip(plan.schema.columns, header.column_types)
                ],
            }

        return await self.warehouse.run(operation)

    async def preview(self, compiled):
        def operation(connect):
            client = connect()
            result = client.query(
                f"SELECT * FROM ({compiled.sql}) LIMIT 50",
                settings=self.execution_settings(),
            )
            return {
                "columns": list(compiled.schema.columns),
                "rows": [
                    [self.warehouse.cell(v) for v in row] for row in result.result_rows
                ],
                "limit": 50,
                "rowCount": int(
                    client.command(
                        f"SELECT count() FROM ({compiled.sql})",
                        settings=self.execution_settings(),
                    )
                ),
                "validation": "sample_only",
            }

        return await self.warehouse.run(operation)
