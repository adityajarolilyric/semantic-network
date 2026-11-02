from abc import ABC, abstractmethod
import asyncio
from datetime import date, datetime
from decimal import Decimal
import json
import logging
import re
from uuid import uuid4

from ..core.config import Settings
from ..core.errors import Conflict, MetadataError, NotFound
from .csv_parser import ParsedCsv
from .schemas import (
    CsvImportResult,
    TableCatalog,
    TablePreview,
    WarehouseColumn,
    WarehouseTable,
)

logger = logging.getLogger("metadata.warehouse")
STAGING_PREFIX = "__csv_ingest_"


class WarehouseUnavailable(MetadataError):
    def __init__(self):
        super().__init__(
            "warehouse_unavailable",
            "Cannot reach ClickHouse. Check the server connection and try again.",
            503,
        )


class WarehouseRepository(ABC):
    @abstractmethod
    async def ping(self) -> None: ...

    @abstractmethod
    async def catalog(self) -> TableCatalog: ...

    @abstractmethod
    async def preview(self, table: str, offset: int, limit: int) -> TablePreview: ...

    @abstractmethod
    async def import_csv(
        self, table: str, parsed: ParsedCsv, import_id: str, fingerprint: str
    ) -> CsvImportResult: ...

    async def reset(self) -> int:
        raise MetadataError(
            "reset_unavailable", "This warehouse does not support workspace reset.", 503
        )

    @abstractmethod
    async def close(self) -> None: ...


def quote_identifier(value: str) -> str:
    return "`" + value.replace("\\", "\\\\").replace("`", "\\`") + "`"


def quote_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def validate_table_name(table: str):
    if (
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", table)
        or table.startswith(STAGING_PREFIX)
        or table.startswith("__semantic_")
        or re.fullmatch(r"grain_[A-Za-z0-9_]+_[a-f0-9]{28}", table)
    ):
        raise MetadataError(
            "invalid_table_name",
            "Table names must start with a letter or underscore and contain only letters, numbers and underscores (maximum 128 characters).",
        )


class ClickHouseWarehouseRepository(WarehouseRepository):
    """One lazy client per app; bounded small-file operations run outside the event loop."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        self.lock = asyncio.Lock()

    async def reset(self) -> int:
        database = self.settings.clickhouse_database
        if database.lower() in ("system", "information_schema"):
            raise MetadataError(
                "invalid_reset_database", "System databases cannot be reset."
            )

        def clear(connect):
            client = connect()
            tables = client.query(
                "SELECT name, engine FROM system.tables WHERE database = "
                + quote_literal(database)
                + " ORDER BY engine = 'View' DESC, name"
            ).result_rows
            for name, engine in tables:
                kind = (
                    "VIEW"
                    if engine in ("View", "MaterializedView", "LiveView", "WindowView")
                    else "TABLE"
                )
                client.command(f"DROP {kind} IF EXISTS {self.qualify(name)} SYNC")
            return len(tables)

        return await self.run(clear)

    def connect(self):
        if self.client is None:
            import clickhouse_connect

            self.client = clickhouse_connect.get_client(
                host=self.settings.clickhouse_host,
                port=self.settings.clickhouse_port,
                username=self.settings.clickhouse_username,
                password=self.settings.clickhouse_password.get_secret_value(),
                database=self.settings.clickhouse_database,
                secure=self.settings.clickhouse_secure,
                connect_timeout=self.settings.clickhouse_timeout_sec,
                send_receive_timeout=self.settings.clickhouse_timeout_sec,
                autogenerate_session_id=False,
            )
        return self.client

    async def run(self, operation):
        async with self.lock:
            try:
                task = asyncio.create_task(asyncio.to_thread(operation, self.connect))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Keep the client guard until the synchronous operation and staging cleanup finish.
                    try:
                        await task
                    except Exception:
                        logger.warning("cancelled_warehouse_operation_failed")
                    raise
            except MetadataError:
                raise
            except Exception as exc:
                for marker, code, message in [
                    (
                        "SEMANTIC_INVALID_WEIGHTS",
                        "invalid_disaggregation_weights",
                        "Dis-aggregation weights must be present and their group total must be nonzero.",
                    ),
                    (
                        "SEMANTIC_MEASURE_OVERFLOW",
                        "measure_numeric_overflow",
                        "A measure result exceeds its declared precision or cannot be represented by its declared integer type.",
                    ),
                    (
                        "SEMANTIC_MEASURE_NULL",
                        "measure_null_result",
                        "A non-nullable measure produced NULL.",
                    ),
                ]:
                    if marker in str(exc):
                        raise MetadataError(code, message) from exc
                if "MEMORY_LIMIT_EXCEEDED" in str(exc):
                    raise MetadataError(
                        "query_resource_limit",
                        "The query exceeded its configured memory limit. Select fewer measures or dimension attributes.",
                    ) from exc
                if "TIMEOUT_EXCEEDED" in str(exc):
                    raise MetadataError(
                        "query_timeout",
                        "The query exceeded its time limit. Select fewer measures or dimension attributes.",
                        504,
                    ) from exc
                if "SEMANTIC_UNMATCHED_DIMENSION" in str(exc):
                    raise MetadataError(
                        "unmatched_dimension_member",
                        "A population row has no matching dimension member.",
                    ) from exc
                if "SEMANTIC_DUPLICATE_PIVOT_CELL" in str(exc):
                    raise MetadataError(
                        "duplicate_pivot_cell",
                        "Duplicate pivot cells must be corrected in the source data.",
                    ) from exc
                logger.error(
                    "warehouse_operation_failed",
                    extra={"error_type": type(exc).__name__},
                )
                raise WarehouseUnavailable() from exc

    def qualify(self, table):
        return f"{quote_identifier(self.settings.clickhouse_database)}.{quote_identifier(table)}"

    def table_info(self, client, table):
        result = client.query(
            "SELECT engine, comment FROM system.tables WHERE database = {db:String} AND name = {table:String}",
            parameters={"db": self.settings.clickhouse_database, "table": table},
        )
        return result.first_row if result.result_rows else None

    def columns(self, client, table):
        result = client.query(
            "SELECT name, type FROM system.columns WHERE database = {db:String} AND table = {table:String} ORDER BY position",
            parameters={"db": self.settings.clickhouse_database, "table": table},
        )
        return [WarehouseColumn(name=row[0], type=row[1]) for row in result.result_rows]

    async def ping(self):
        await self.run(lambda connect: connect().command("SELECT 1"))

    async def catalog(self):
        def operation(connect):
            client = connect()
            rows = client.query(
                """SELECT t.name, t.engine, t.total_rows, count(c.name)
                FROM system.tables AS t LEFT JOIN system.columns AS c ON t.database = c.database AND t.name = c.table
                WHERE t.database = {db:String} AND t.is_temporary = 0 AND NOT startsWith(t.name, {prefix:String}) AND NOT startsWith(t.name, '.') AND NOT startsWith(t.name, '__semantic_') AND NOT startsWith(t.comment, 'semanticTable:') AND NOT startsWith(t.comment, 'semanticArtifact:')
                GROUP BY t.name, t.engine, t.total_rows ORDER BY t.name LIMIT 1001""",
                parameters={
                    "db": self.settings.clickhouse_database,
                    "prefix": STAGING_PREFIX,
                },
            ).result_rows
            return TableCatalog(
                tables=[
                    WarehouseTable(
                        name=row[0], engine=row[1], rowCount=row[2], columnCount=row[3]
                    )
                    for row in rows[:1000]
                ],
                truncated=len(rows) > 1000,
            )

        return await self.run(operation)

    @staticmethod
    def cell(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    async def preview(self, table, offset, limit):
        def operation(connect):
            client = connect()
            info = self.table_info(client, table)
            if not info or info[1].startswith(("semanticTable:", "semanticArtifact:")):
                raise NotFound("Source table")
            columns = self.columns(client, table)
            total = int(client.command(f"SELECT count() FROM {self.qualify(table)}"))
            selected = ", ".join(quote_identifier(column.name) for column in columns)
            # Deterministic value ordering keeps small uploaded datasets stable across pages.
            result = client.query(
                f"SELECT {selected} FROM {self.qualify(table)} ORDER BY ALL LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}",
                parameters={"limit": limit, "offset": offset},
                settings={
                    "max_execution_time": self.settings.clickhouse_timeout_sec,
                    "max_result_rows": limit,
                    "result_overflow_mode": "throw",
                },
            )
            return TablePreview(
                table=table,
                columns=columns,
                rows=[
                    [self.cell(value) for value in row] for row in result.result_rows
                ],
                total=total,
                offset=offset,
                limit=limit,
            )

        return await self.run(operation)

    @staticmethod
    def same_import(comment, import_id, fingerprint):
        try:
            marker = json.loads(comment).get("semanticCsvImport", {})
            return marker.get("id") == import_id and marker.get("sha256") == fingerprint
        except (ValueError, AttributeError, TypeError):
            return False

    async def import_csv(self, table, parsed, import_id, fingerprint):
        def operation(connect):
            client = connect()
            existing = self.table_info(client, table)
            if existing:
                if self.same_import(existing[1], import_id, fingerprint):
                    return CsvImportResult(
                        table=table,
                        rowCount=int(
                            client.command(f"SELECT count() FROM {self.qualify(table)}")
                        ),
                        columns=self.columns(client, table),
                        replayed=True,
                    )
                raise Conflict(
                    f"A table named '{table}' already exists. Choose another table name."
                )
            staging = STAGING_PREFIX + uuid4().hex
            comment = json.dumps(
                {"semanticCsvImport": {"id": import_id, "sha256": fingerprint}}
            )
            created = False
            try:
                declarations = ", ".join(
                    f"{quote_identifier(column.name)} {column.type}"
                    for column in parsed.columns
                )
                client.command(
                    f"CREATE TABLE {self.qualify(staging)} ({declarations}) ENGINE = MergeTree ORDER BY tuple() COMMENT {quote_literal(comment)}",
                )
                created = True
                if parsed.rows:
                    client.insert(
                        staging,
                        parsed.rows,
                        column_names=[column.name for column in parsed.columns],
                        database=self.settings.clickhouse_database,
                    )
                count = int(
                    client.command(f"SELECT count() FROM {self.qualify(staging)}")
                )
                if count != len(parsed.rows):
                    raise MetadataError(
                        "import_verification_failed",
                        "Imported row count did not match the CSV; the table was not published.",
                        503,
                    )
                try:
                    client.command(
                        f"RENAME TABLE {self.qualify(staging)} TO {self.qualify(table)}"
                    )
                except Exception:
                    target = self.table_info(client, table)
                    if target and self.same_import(target[1], import_id, fingerprint):
                        return CsvImportResult(
                            table=table,
                            rowCount=count,
                            columns=parsed.columns,
                            replayed=True,
                        )
                    if target:
                        raise Conflict(
                            f"A table named '{table}' already exists. Choose another table name."
                        ) from None
                    raise
                created = False
                warnings = (
                    [
                        "The CSV contains headers only; all columns were created as nullable strings."
                    ]
                    if not parsed.rows
                    else []
                )
                return CsvImportResult(
                    table=table,
                    rowCount=count,
                    columns=parsed.columns,
                    warnings=warnings,
                )
            finally:
                if created:
                    try:
                        client.command(f"DROP TABLE IF EXISTS {self.qualify(staging)}")
                    except Exception:
                        logger.warning("csv_staging_cleanup_failed")

        return await self.run(operation)

    async def close(self):
        async with self.lock:
            if self.client is not None:
                await asyncio.to_thread(self.client.close)
                self.client = None
