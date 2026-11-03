from .table_storage import GENERATION, owner, relation_sql
from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q, quote_literal


class PlanningTableExecutor:
    def __init__(self, warehouse, settings):
        self.warehouse, self.settings = warehouse, settings

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
        def operation(connect):
            for check in compiled.checks:
                rows = (
                    connect()
                    .query(check.sql, settings=self.execution_settings())
                    .result_rows
                )
                if rows:
                    raise MetadataError(
                        check.code,
                        {
                            "resolution_profiles_changed": "Source matching profiles changed during execution. Retry the build or preview.",
                            "ambiguous_resolution_time_order": "First/Last requires calendar ordering values that distinguish dates within each native period.",
                            "invalid_resolution_calendar": "The daily calendar contains an invalid date or a null/incompatible native period key.",
                            "ambiguous_resolution_calendar": "A calendar date maps to more than one native period. Filter the Time dimension to an unambiguous calendar.",
                            "invalid_resolution_range": "Start and End must be valid ISO dates, and Start must not be after End.",
                            "resolution_calendar_coverage": "The date range extends outside the available Time dimension, or the calendar is empty.",
                            "resolution_native_coverage": "A calendar date in the range maps to a missing native planning intersection. Rebuild or extend the planning population; ranges are not silently clipped.",
                            "unmatched_resolution_range": "The date range contains no selected calendar dates.",
                            "empty_self_weights": "Weighted by self has no populated values in this edited cell. Choose another disaggregation policy to initialise it.",
                            "zero_self_weight_total": "Weighted by self cannot distribute this edit because its populated values total zero.",
                            "invalid_edit_member": "A pivot member is incompatible with its dimension type. Refresh the view.",
                            "empty_edit_scope": "This pivot cell has no eligible intersections at the measure's write level. No edit was applied.",
                            "null_resolution_order": "Application order cannot be NULL. Choose a populated column with unique values for overlapping assignments.",
                            "ambiguous_resolution_order": "Overlapping assignments have the same complete application order. Add a tie-breaker column.",
                            "invalid_resolution_literal": "A matching value cannot be represented by the target attribute's data type.",
                            "unmatched_resolution_row": "A transaction assignment matches no valid native planning intersection.",
                            "resolution_expansion_limit": "Transaction resolution exceeds its configured expansion limit. Add more selective matching constraints.",
                            "unmatched_transaction_grain": "A transaction key combination is absent from its native planning population.",
                            "no_disaggregation_children": "A populated native measure cell has no matching Base rows.",
                            "ambiguous_dimension_lookup": "The mapped dimension values have conflicting combinations of the selected attributes. Select attributes determined by the mapping; extra attributes do not change the planning grain.",
                        }.get(
                            check.code,
                            "Dimension mappings must match exactly one member per population row.",
                        ),
                        details=[
                            {
                                "message": str([self.warehouse.cell(v) for v in row])[
                                    :1000
                                ]
                            }
                            for row in rows[:20]
                        ],
                    )

        await self.warehouse.run(operation)

    async def build(self, artifact, compiled, plan):
        storage = getattr(self, "storage", None)
        storage_columns = (
            await storage.prepare(artifact, compiled, self.execution_settings())
            if artifact.get("storageMode") == "versioned"
            else None
        )

        def operation(connect):
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
            # The SELECT header determines authoritative ClickHouse types without reading rows.
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
                client.command(
                    f"INSERT INTO {table} SELECT * FROM ({compiled.sql}) LIMIT {self.settings.planning_max_rows + 1}",
                    settings=self.execution_settings(),
                )
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
                "columns": [
                    {
                        **c,
                        **(
                            {"storageName": storage_columns[index]["storageName"]}
                            if storage_columns
                            else {}
                        ),
                        "physicalType": typ.name.removeprefix("Nullable(").removesuffix(
                            ")"
                        )
                        if typ.name.startswith("Nullable(")
                        else typ.name,
                        "nullable": typ.name.startswith("Nullable("),
                    }
                    for index, (c, typ) in enumerate(
                        zip(plan.schema.columns, header.column_types)
                    )
                ],
            }

        return await self.warehouse.run(operation)

    async def preview(self, compiled):
        def operation(connect):
            result = connect().query(
                f"SELECT * FROM ({compiled.sql}) LIMIT 100",
                settings=self.execution_settings(),
            )
            return {
                "columns": list(compiled.schema.columns),
                "rows": [
                    [self.warehouse.cell(v) for v in row] for row in result.result_rows
                ],
                "limit": 100,
                "validation": "sample_only",
            }

        return await self.warehouse.run(operation)
