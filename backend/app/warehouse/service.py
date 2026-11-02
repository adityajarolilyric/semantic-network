import asyncio
import hashlib

from ..core.config import Settings
from ..core.errors import MetadataError
from .csv_parser import CsvParser
from .repository import WarehouseRepository, WarehouseUnavailable, validate_table_name
from .schemas import WarehouseStatus


class WarehouseSourceService:
    def __init__(self, repository: WarehouseRepository, settings: Settings):
        self.repository = repository
        self.settings = settings
        self.parser = CsvParser(max_rows=settings.csv_max_rows)

    async def status(self):
        try:
            await self.repository.ping()
            return WarehouseStatus(
                connected=True,
                database=self.settings.clickhouse_database,
                maxCsvRows=self.settings.csv_max_rows,
                maxFileBytes=self.settings.csv_max_file_bytes,
            )
        except WarehouseUnavailable:
            return WarehouseStatus(
                connected=False,
                database=None,
                maxCsvRows=self.settings.csv_max_rows,
                maxFileBytes=self.settings.csv_max_file_bytes,
                message="ClickHouse is unavailable. Check the server connection and retry.",
            )

    async def catalog(self):
        return await self.repository.catalog()

    async def preview(self, table, offset, limit):
        if (
            not table
            or len(table) > 255
            or table.startswith("__csv_ingest_")
            or any(ord(char) < 32 for char in table)
        ):
            raise MetadataError(
                "invalid_table_name", "Choose a valid source table from the catalog."
            )
        return await self.repository.preview(table, offset, limit)

    async def import_csv(self, table, filename, content, import_id):
        validate_table_name(table)
        if not filename or not filename.lower().endswith(".csv"):
            raise MetadataError("invalid_file_type", "Choose a .csv file.")
        if len(content) > self.settings.csv_max_file_bytes:
            raise MetadataError(
                "file_too_large",
                "The CSV exceeds the configured upload size limit.",
                413,
            )
        parsed = await asyncio.to_thread(self.parser.parse, content)
        fingerprint = hashlib.sha256(content).hexdigest()
        return await self.repository.import_csv(table, parsed, import_id, fingerprint)
