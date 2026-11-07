"""Fenced, database-side measure checkpoints with bounded scalar diagnostics."""

from dataclasses import replace
from time import perf_counter

from ..core.errors import MetadataError
from ..engine.in_place_values import update_query, write_groups
from ..engine.snapshot_reads import snapshot_read
from .measure_columns import MeasureColumnSql
from .sql import quote_identifier as q


class InPlaceValuesWriter:
    def __init__(self, warehouse, settings):
        self.warehouse, self.settings = warehouse, settings
        self.column_sql = MeasureColumnSql(warehouse)

    async def build(self, artifact, compiled, plan, columns, settings, *, guard):
        started = perf_counter()
        keys = [c for c in columns if c["columnId"] in plan.key_ids]
        changed = [c for c in columns if c["columnId"] in artifact["updateColumnIds"]]
        working = replace(compiled, query=compiled.query.copy())
        table = self.warehouse.qualify(artifact["physicalName"])
        snapshot = {
            **artifact,
            "columns": columns,
            "keyColumns": {c["columnId"]: c["storageName"] for c in keys},
            "measureColumns": {
                c["measureId"]: c["storageName"] for c in columns if c.get("measureId")
            },
            "coverageColumns": {
                c["coverageFor"]: c["storageName"]
                for c in columns
                if c.get("coverageFor")
            },
            "mixedColumns": {
                c["mixedFor"]: c["storageName"] for c in columns if c.get("mixedFor")
            },
        }

        def setup(connect):
            if artifact.get("storageMode") != "current":
                raise MetadataError(
                    "storage_migration_required",
                    "Migrate legacy values to current storage before rebuilding.",
                    409,
                )
            client = connect()
            info = self.warehouse.table_info(client, artifact["physicalName"])
            if not info or info[0] != "MergeTree" or info[1] != artifact["buildOwner"]:
                raise MetadataError(
                    "artifact_ownership", "The build clone ownership changed.", 409
                )
            return int(
                client.command(f"SELECT count() FROM {table}", settings=settings)
            )

        await guard(artifact)
        count = await self.warehouse.run(setup)
        if count > self.settings.planning_max_rows:
            raise MetadataError(
                "planning_row_limit", "Values exceed the configured row limit."
            )
        measure_timings = {}
        group_timings = []
        groups = write_groups(artifact, changed)
        for group_index, group in enumerate(groups):
            output_columns = [
                c
                for c in changed
                if any(
                    mid in (c.get("measureId"), c.get("coverageFor"), c.get("mixedFor"))
                    for mid in group
                )
            ]
            await guard(artifact)
            measure_started = perf_counter()
            initial = bool(artifact.get("populateWithValues")) and group_index == 0
            selected_columns = output_columns
            if initial:
                selected_columns = [
                    c
                    for c in columns
                    if not (
                        c.get("measureId") or c.get("coverageFor") or c.get("mixedFor")
                    )
                ] + output_columns
            narrow = update_query(
                working, plan.key_ids, [c["columnId"] for c in selected_columns]
            )

            def apply(connect):
                client = connect()
                if initial:
                    targets = ", ".join(q(c["storageName"]) for c in selected_columns)
                    selected = ", ".join(q(c["name"]) for c in selected_columns)
                    client.command(
                        f"INSERT INTO {table} ({targets}) SELECT {selected} FROM ({narrow}) LIMIT {self.settings.planning_max_rows + 1}",
                        settings=settings,
                    )
                    projection = ", ".join(
                        q(c["storageName"]) + " AS " + q(c["name"]) for c in columns
                    )
                    return self.column_sql.validate(
                        client,
                        f"SELECT {projection} FROM {table}",
                        keys,
                        selected_columns,
                        settings,
                    )
                self.column_sql.write(
                    client, artifact, narrow, keys, output_columns, settings, count
                )
                return count

            count = await self.warehouse.run(apply)
            if count > self.settings.planning_max_rows:
                raise MetadataError(
                    "planning_row_limit", "Values exceed the configured row limit."
                )
            elapsed = round((perf_counter() - measure_started) * 1000, 3)
            group_timings.append(
                {
                    "measureIds": group,
                    "elapsed": elapsed,
                    "operation": "insert_select" if initial else "column_update",
                }
            )
            clause = working.query.args.get("with")
            for mid in group:
                if clause:
                    read = snapshot_read(
                        table, snapshot, mid, plan.key_ids, physical=True
                    )
                    for cte in clause.expressions:
                        if cte.alias == artifact["measureNodes"].get(mid):
                            cte.set("this", read.query.copy())
                measure_timings[mid] = elapsed

        def inspect(connect):
            client = connect()
            coverage = [c for c in columns if c.get("coverageFor")]
            incomplete = {}
            if coverage:
                counts = client.query(
                    "SELECT "
                    + ", ".join(f"countIf({q(c['storageName'])}=0)" for c in coverage)
                    + f" FROM {table}",
                    settings=settings,
                ).first_row
                incomplete = {
                    c["coverageFor"]: int(n) for c, n in zip(coverage, counts) if n
                }
            size = int(
                client.command(
                    "SELECT coalesce(sum(bytes_on_disk),0) FROM system.parts WHERE database={db:String} AND table={table:String} AND active",
                    parameters={
                        "db": self.settings.clickhouse_database,
                        "table": artifact["physicalName"],
                    },
                )
            )
            return incomplete, size

        await guard(artifact)
        incomplete, size = await self.warehouse.run(inspect)
        if size > self.settings.planning_max_bytes:
            raise MetadataError(
                "planning_byte_limit",
                "Values output exceeds the configured storage limit.",
            )
        return {
            "rowCount": count,
            "columns": columns,
            "bytesOnDisk": size,
            "incompleteMeasures": incomplete,
            "buildTimingsMs": {
                "total": round((perf_counter() - started) * 1000, 3),
                "measures": measure_timings,
                "groups": group_timings,
            },
            "writeMode": artifact["writeMode"],
            "writer": "clickhouse_columns",
            "writeGroups": groups,
            "updatedColumns": [c["columnId"] for c in changed],
        }
