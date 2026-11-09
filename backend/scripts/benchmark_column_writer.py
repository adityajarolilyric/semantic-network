"""Exercise column writes in a disposable database; never change live tables.

Run from the repository root:
  backend/.venv/bin/python -m backend.scripts.benchmark_column_writer --rows 100000
"""

import argparse
import asyncio
from dataclasses import dataclass
import importlib.util
import json
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

from sqlglot import parse_one

from backend.app.core.config import Settings
from backend.app.engine.sql.contracts import RelationSchema
from backend.app.warehouse.in_place_values import InPlaceValuesWriter
from backend.app.warehouse.repository import ClickHouseWarehouseRepository


@dataclass
class Query:
    query: object
    schema: RelationSchema


class CountedClient:
    def __init__(self, client, counters):
        self.client, self.counters = client, counters

    def command(self, sql, *args, **kwargs):
        if sql.startswith("ALTER TABLE") and " UPDATE " in sql:
            self.counters["mutationCommands"] += 1
        return self.client.command(sql, *args, **kwargs)

    def query(self, sql, *args, **kwargs):
        result = self.client.query(sql, *args, **kwargs)
        counters = self.counters

        class Result:
            @property
            def result_rows(self):
                rows = result.result_rows
                if "system." not in sql:
                    counters["bulkRowsFetched"] += len(rows)
                return rows

            def __getattr__(self, name):
                return getattr(result, name)

        return Result()

    def __getattr__(self, name):
        return getattr(self.client, name)


async def benchmark(rows, baseline_writer=None, fractional=False):
    settings = Settings()
    admin = ClickHouseWarehouseRepository(settings)
    database = "semantic_column_bench_" + uuid4().hex[:12]
    settings = settings.model_copy(update={"clickhouse_database": database})
    warehouse = ClickHouseWarehouseRepository(settings)
    columns = [
        {
            "columnId": "pid",
            "name": "pid",
            "storageName": "pid",
            "physicalType": "String",
            "nullable": False,
        },
        {
            "columnId": "qty",
            "measureId": "qty",
            "name": "qty",
            "storageName": "qty",
            "physicalType": "Float64",
            "nullable": True,
        },
        {
            "columnId": "existing",
            "measureId": "existing",
            "name": "existing",
            "storageName": "existing",
            "physicalType": "Float64",
            "nullable": True,
        },
    ]
    value_expression = (
        "round(toFloat64(number)/7,6)" if fractional else "toFloat64(number%997)"
    )
    stored_expression = value_expression.replace("number", "toUInt64(pid)")
    query = Query(
        parse_one(
            f"SELECT toString(number) AS pid, {value_expression} AS qty FROM numbers({rows})",
            read="clickhouse",
        ),
        RelationSchema(tuple(columns)),
    )
    writers = [("clickhouse", InPlaceValuesWriter(warehouse, settings))]
    if baseline_writer:
        spec = importlib.util.spec_from_file_location(
            "backend.app.warehouse.baseline_benchmark", baseline_writer
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        writers.insert(0, ("baseline", module.InPlaceValuesWriter(warehouse, settings)))
    budget = {
        "max_memory_usage": 512 * 1024**2,
        "max_threads": 4,
        "join_use_nulls": 1,
        "max_execution_time": settings.clickhouse_timeout_sec,
    }
    results = []

    async def guard(_):
        return None

    try:
        await admin.run(
            lambda connect: connect().command(
                f"CREATE DATABASE `{database}` ENGINE=Atomic"
            )
        )
        for label, writer in writers:
            name = "candidate_" + label
            table = warehouse.qualify(name)
            await warehouse.run(
                lambda connect: connect().command(
                    f"CREATE TABLE {table} (pid String, qty Nullable(Float64), existing Nullable(Float64)) ENGINE=MergeTree ORDER BY pid COMMENT 'benchmark-owner'"
                )
            )
            await warehouse.run(
                lambda connect: connect().command(
                    f"INSERT INTO {table} SELECT toString(number), NULL, 120 FROM numbers({rows})"
                )
            )
            artifact = {
                "physicalName": name,
                "buildOwner": "benchmark-owner",
                "storageMode": "current",
                "writeMode": "in_place",
                "updateColumnIds": ["qty"],
                "measureOrder": ["qty"],
                "measureNodes": {},
                "measureDependencies": {"qty": []},
            }
            counters = {"mutationCommands": 0, "bulkRowsFetched": 0}
            original_run = warehouse.run

            async def counted_run(operation):
                return await original_run(
                    lambda connect: operation(
                        lambda: CountedClient(connect(), counters)
                    )
                )

            warehouse.run = counted_run
            start = perf_counter()
            try:
                await writer.build(
                    artifact,
                    query,
                    SimpleNamespace(key_ids=("pid",)),
                    columns,
                    budget,
                    guard=guard,
                )
            finally:
                elapsed = perf_counter() - start
                warehouse.run = original_run
            summary = await warehouse.run(
                lambda connect: connect()
                .query(
                    f"SELECT count(), countIf(isNull(existing) OR existing!=120), countIf(isNull(qty) OR qty!={stored_expression}), groupBitXor(cityHash64(tuple(pid,qty,existing))) FROM {table}"
                )
                .first_row
            )
            assert summary[0] == rows and summary[1:3] == (0, 0), summary
            result = {
                "writer": label,
                "rows": rows,
                "seconds": round(elapsed, 3),
                "checksum": str(summary[3]),
                "existingEditsPreserved": True,
                **counters,
            }
            results.append(result)
            print(json.dumps(result), flush=True)
        assert len({r["checksum"] for r in results}) == 1
        return results
    finally:
        await warehouse.close()
        await admin.run(
            lambda connect: connect().command(
                f"DROP DATABASE IF EXISTS `{database}` SYNC"
            )
        )
        await admin.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument(
        "--baseline-writer",
        help="Optional previous writer file for a controlled local comparison",
    )
    parser.add_argument(
        "--fractional",
        action="store_true",
        help="Exercise six-decimal fractional values",
    )
    args = parser.parse_args()
    if not 1 <= args.rows <= 1000000:
        parser.error("--rows must be between 1 and 1000000")
    asyncio.run(benchmark(args.rows, args.baseline_writer, args.fractional))
