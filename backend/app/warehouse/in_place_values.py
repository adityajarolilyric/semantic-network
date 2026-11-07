"""Bounded column writes with synchronous mutations and between-batch fencing."""

from dataclasses import replace
from time import perf_counter

from ..core.errors import MetadataError
from ..engine.in_place_values import update_query
from ..engine.snapshot_reads import snapshot_read
from .sql import quote_identifier as q, quote_literal as lit
from .versioned_storage import GENERATION, owner, token


def key_expression(columns, field):
    return "toJSONString(tuple(" + ", ".join(q(c[field]) for c in columns) + "))"


def value_text(value):
    if value is None:
        return ""
    return str(int(value)) if isinstance(value, bool) else str(value)


class InPlaceValuesWriter:
    batch_size = 1000

    def __init__(self, warehouse, settings):
        self.warehouse, self.settings = warehouse, settings

    def mutate(self, client, table, generation, stored_key, batch, changed, settings):
        params = {"keys": [row[0] for row in batch]}
        assignments = []
        for index, column in enumerate(changed, 1):
            params[f"values{index}"] = [value_text(row[index]) for row in batch]
            params[f"nulls{index}"] = [int(row[index] is None) for row in batch]
            value = f"CAST(transform({stored_key}, {{keys:Array(String)}}, {{values{index}:Array(String)}}, ''), 'Nullable({column['physicalType']})')"
            value = f"if(transform({stored_key}, {{keys:Array(String)}}, {{nulls{index}:Array(UInt8)}}, toUInt8(0))=1, NULL, {value})"
            assignments.append(q(column["storageName"]) + "=" + value)
        client.command(
            f"ALTER TABLE {table} UPDATE {', '.join(assignments)} WHERE {generation} AND {stored_key} IN {{keys:Array(String)}}",
            parameters=params,
            settings={**settings, "mutations_sync": 2},
        )

    async def build(self, artifact, compiled, plan, columns, settings, *, guard):
        started = perf_counter()
        keys = [c for c in columns if c["columnId"] in plan.key_ids]
        changed = [c for c in columns if c["columnId"] in artifact["updateColumnIds"]]
        working = replace(compiled, query=compiled.query.copy())
        table = self.warehouse.qualify(artifact["physicalName"])
        generation = "1" if artifact.get("storageMode") == "current" else f"{q(GENERATION)}={lit(token(artifact))}"
        stored_key = key_expression(keys, "storageName")
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
            client = connect()
            info = self.warehouse.table_info(client, artifact["physicalName"])
            if artifact.get("storageMode") == "current":
                if not info or info[0] != "MergeTree" or info[1] != artifact["buildOwner"]:
                    raise MetadataError("artifact_ownership", "The build clone ownership changed.", 409)
                return int(client.command(f"SELECT count() FROM {table}", settings=settings))
            if not info and artifact["writeMode"] == "column_build":
                client.command(
                    f"CREATE TABLE {table} ({q(GENERATION)} String) ENGINE=MergeTree ORDER BY ({q(GENERATION)}) COMMENT {lit(owner(artifact))}"
                )
                info = self.warehouse.table_info(client, artifact["physicalName"])
            if not info or info[0] != "MergeTree" or info[1] != owner(artifact):
                raise MetadataError(
                    "artifact_ownership",
                    "The values table is missing or its ownership changed.",
                    409,
                )
            if artifact.get("recoveryBuild"):
                # A timed-out mutation may still be running after its job failed.
                # Cancel only engine-owned table mutations; recovery uses a fresh
                # generation and recomputes the complete previous dirty closure.
                client.command(
                    "KILL MUTATION WHERE database={db:String} AND table={table:String} AND is_done=0",
                    parameters={
                        "db": self.settings.clickhouse_database,
                        "table": artifact["physicalName"],
                    },
                )
            client.command(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {q(GENERATION)} String DEFAULT {lit(artifact.get('legacyGeneration') or token(artifact))}"
            )
            for column in columns:
                client.command(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {q(column['storageName'])} Nullable({column['physicalType']})"
                )
            if artifact["writeMode"] == "column_build":
                population = [
                    c
                    for c in columns
                    if not (
                        c.get("measureId") or c.get("coverageFor") or c.get("mixedFor")
                    )
                ]
                target = ", ".join(
                    [q(GENERATION), *(q(c["storageName"]) for c in population)]
                )
                selected = ", ".join(
                    [lit(token(artifact)), *(q(c["name"]) for c in population)]
                )
                # reserve() allocates a fresh generation token for every attempt.
                client.command(
                    f"INSERT INTO {table} ({target}) SELECT {selected} FROM ({artifact['populationQuery']}) LIMIT {self.settings.planning_max_rows + 1}",
                    settings=settings,
                )
            return int(
                client.command(
                    f"SELECT count() FROM {table} WHERE {generation}", settings=settings
                )
            )

        await guard(artifact)
        count = await self.warehouse.run(setup)
        if count > self.settings.planning_max_rows:
            raise MetadataError(
                "planning_row_limit", "Values exceed the configured row limit."
            )
        measure_timings = {}
        for mid in artifact["measureOrder"]:
            output_columns = [
                c
                for c in changed
                if mid in (c.get("measureId"), c.get("coverageFor"), c.get("mixedFor"))
            ]
            if not output_columns:
                continue
            await guard(artifact)
            measure_started = perf_counter()
            narrow = update_query(
                working, plan.key_ids, [c["columnId"] for c in output_columns]
            )
            selected = ", ".join(q(c["name"]) for c in output_columns)
            rows = await self.warehouse.run(
                lambda connect: connect()
                .query(
                    f"SELECT {key_expression(keys, 'name')} AS __update_key, {selected} FROM ({narrow}) LIMIT {self.settings.planning_max_rows + 1}",
                    settings={
                        **settings,
                        "max_result_bytes": self.settings.planning_max_bytes,
                        "result_overflow_mode": "throw",
                    },
                )
                .result_rows
            )
            if len(rows) != count or len({r[0] for r in rows}) != count:
                raise MetadataError(
                    "measure_cardinality",
                    "Column updates must cover each native intersection exactly once.",
                )
            for index, column in enumerate(output_columns, 1):
                if not column["nullable"] and any(row[index] is None for row in rows):
                    raise MetadataError(
                        "measure_null_result", "A non-nullable measure produced NULL."
                    )
            for offset in range(0, len(rows), self.batch_size):
                await guard(artifact)
                batch = rows[offset : offset + self.batch_size]
                await self.warehouse.run(
                    lambda connect: self.mutate(
                        connect(),
                        table,
                        generation,
                        stored_key,
                        batch,
                        output_columns,
                        settings,
                    )
                )
            stored_values = ", ".join(q(c["storageName"]) for c in output_columns)
            actual = await self.warehouse.run(
                lambda connect: connect()
                .query(
                    f"SELECT {stored_key}, {stored_values} FROM {table} WHERE {generation}",
                    settings=settings,
                )
                .result_rows
            )
            if {r[0]: tuple(r[1:]) for r in actual} != {
                r[0]: tuple(r[1:]) for r in rows
            }:
                raise MetadataError(
                    "measure_update_mismatch",
                    "Persisted measure values do not match the completed update.",
                )
            clause = working.query.args.get("with")
            if clause:
                read = snapshot_read(table, snapshot, mid, plan.key_ids, physical=True)
                for cte in clause.expressions:
                    if cte.alias == artifact["measureNodes"].get(mid):
                        cte.set("this", read.query.copy())
            measure_timings[mid] = round((perf_counter() - measure_started) * 1000, 3)

        def inspect(connect):
            client = connect()
            coverage = [c for c in columns if c.get("coverageFor")]
            incomplete = {}
            if coverage:
                counts = client.query(
                    "SELECT "
                    + ", ".join(f"countIf({q(c['storageName'])}=0)" for c in coverage)
                    + f" FROM {table} WHERE {generation}",
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
            },
            "writeMode": artifact["writeMode"],
            "updatedColumns": [c["columnId"] for c in changed],
        }
