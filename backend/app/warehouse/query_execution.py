"""Client access and bounded read execution; callers supply compiled plans."""

from ..core.errors import Conflict, MetadataError
from ..engine.sql.storage import owner, relation_sql
from .sql import quote_identifier as q, quote_literal as lit


class WarehouseCheckRunner:
    def __init__(self, warehouse):
        self.warehouse = warehouse

    async def run(self, checks, settings, diagnostics):
        def execute(connect):
            for check in checks:
                rows = connect().query(check.sql, settings=settings).result_rows
                if rows:
                    diagnostics.raise_failure(check.code, rows, self.warehouse.cell)

        await self.warehouse.run(execute)


class WarehouseQueryExecutor:
    def __init__(self, warehouse):
        self.warehouse = warehouse

    async def stage_edit_cells(self, artifact, query, columns, settings):
        """One owned, transient disk checkpoint; no cell values enter Python."""

        def create(connect):
            client = connect()
            if self.warehouse.table_info(client, artifact["physicalName"]):
                raise Conflict("The edit cell stage already exists.")
            declarations = ", ".join(
                q(column["name"])
                + " "
                + (
                    "Nullable(" + column["physicalType"] + ")"
                    if column.get("nullable")
                    and not column["physicalType"].startswith("Nullable(")
                    else column["physicalType"]
                )
                for column in columns
            )
            table = self.warehouse.qualify(artifact["physicalName"])
            client.command(
                f"CREATE TABLE {table} ({declarations}) ENGINE=MergeTree ORDER BY tuple() COMMENT {lit(artifact['buildOwner'])}"
            )
            client.command(f"INSERT INTO {table} {query}", settings=settings)

        await self.warehouse.run(create)

    async def pivot_profile(self, artifact, settings, limit):
        def read(connect):
            columns = ", ".join(q(column["name"]) for column in artifact["columns"])
            return (
                connect()
                .query(
                    f"SELECT {columns} FROM {self.warehouse.qualify(artifact['physicalName'])} LIMIT {int(limit)}",
                    settings=settings,
                )
                .result_rows
            )

        return await self.warehouse.run(read)

    def preview_sql(self, artifact, columns=None):
        selected = {**artifact, "columns": columns} if columns is not None else artifact
        return relation_sql(self.warehouse, selected) + " ORDER BY ALL LIMIT 50"

    @staticmethod
    def decision_view_sql(compiled, max_groups):
        return (
            compiled.query.copy()
            .order_by("ALL NULLS LAST")
            .limit(max_groups + 1)
            .sql(dialect="clickhouse")
        )

    async def _preview_artifact(
        self, artifact, settings, *, columns=None, ownership_message=None
    ):
        def read(connect):
            client = connect()
            if ownership_message:
                info = self.warehouse.table_info(client, artifact["physicalName"])
                expected_engine = (
                    "View" if artifact.get("storageMode") == "source" else "MergeTree"
                )
                if not info or info[0] != expected_engine or info[1] != owner(artifact):
                    raise Conflict(ownership_message)
            selected = (
                {**artifact, "columns": columns} if columns is not None else artifact
            )
            rows = client.query(
                self.preview_sql(selected),
                settings=settings,
            ).result_rows
            return [[self.warehouse.cell(value) for value in row] for row in rows]

        return await self.warehouse.run(read)

    async def preview_population(self, artifact, settings):
        return await self._preview_artifact(
            artifact,
            settings,
            ownership_message="The planning table is missing or changed. Build it again in a draft.",
        )

    async def output_row_count(self, artifact, settings):
        return await self.warehouse.run(
            lambda connect: int(
                connect().command(
                    f"SELECT count() FROM {self.warehouse.qualify(artifact['physicalName'])}",
                    settings=settings,
                )
            )
        )

    async def preview_values(self, artifact, columns, settings):
        return await self._preview_artifact(
            artifact,
            settings,
            columns=columns,
            ownership_message="The built measure table is missing or changed.",
        )

    async def preview_draft_population(self, artifact, settings):
        return await self._preview_artifact(artifact, settings)

    async def decision_view(self, compiled, settings, max_groups):
        def read(connect):
            rows = (
                connect()
                .query(
                    self.decision_view_sql(compiled, max_groups),
                    settings=settings,
                )
                .result_rows
            )
            if len(rows) > max_groups:
                raise MetadataError(
                    "decision_view_limit",
                    "This pivot has too many groups. Choose fewer attributes or a coarser grain.",
                )
            return [[self.warehouse.cell(value) for value in row] for row in rows]

        return await self.warehouse.run(read)

    async def resolution_preview(self, plan, settings):
        def read(connect):
            client = connect()
            rows = client.query(plan.rows_sql, settings=settings).result_rows

            def count(sql):
                return int(client.command(sql, settings=settings))

            input_rows = count(plan.input_count_sql)
            expanded = (
                count(plan.expanded_count_sql)
                if plan.expanded_count_sql
                else input_rows
            )
            matched = count(plan.matched_count_sql)
            total = count(plan.resolved_count_sql)
            winners = count(plan.winner_count_sql) if plan.winner_count_sql else total
            latest = sum(count(sql) for sql in plan.latest_count_sql)
            return [[self.warehouse.cell(value) for value in row] for row in rows], {
                "inputRows": input_rows,
                "expandedInputRows": expanded,
                "profiles": plan.profile_count,
                "matchedIntersections": matched,
                "resolvedRows": total,
                "overwrittenMatches": matched - winners,
                "filteredOutRows": winners - total,
                "shadowedAssignments": max(0, expanded - latest),
            }

        return await self.warehouse.run(read)

    async def resolution_profiles(self, sql, settings):
        def read(connect):
            return [
                tuple(int(value) for value in row[0])
                for row in connect().query(sql, settings=settings).result_rows
            ]

        return await self.warehouse.run(read)
