from typing import Literal

from pydantic import Field

from ..schemas.common import Schema


class WarehouseColumn(Schema):
    name: str
    type: str


class WarehouseTable(Schema):
    name: str
    engine: str
    rowCount: int | None
    columnCount: int


class TableCatalog(Schema):
    tables: list[WarehouseTable]
    truncated: bool = False


class WarehouseStatus(Schema):
    connected: bool
    database: str | None
    maxCsvRows: int
    maxFileBytes: int
    message: str | None = None


class TablePreview(Schema):
    sql: str | None = None
    table: str
    columns: list[WarehouseColumn]
    rows: list[list[str | None]]
    total: int
    offset: int
    limit: int


class CsvImportResult(Schema):
    table: str
    rowCount: int
    columns: list[WarehouseColumn]
    replayed: bool = False
    semanticSetupComplete: bool = True
    warnings: list[str] = Field(default_factory=list)
    status: Literal["created"] = "created"
