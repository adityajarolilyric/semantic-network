from ..app.core.errors import Conflict, NotFound
from ..app.warehouse.repository import (
    ClickHouseWarehouseRepository,
    WarehouseRepository,
    WarehouseUnavailable,
)
from ..app.warehouse.schemas import (
    CsvImportResult,
    TableCatalog,
    TablePreview,
    WarehouseTable,
)


class FakeWarehouse(WarehouseRepository):
    def __init__(self):
        self.tables = {}
        self.available = True

    async def reset(self):
        await self.ping()
        count = len(self.tables)
        self.tables.clear()
        return count

    async def ping(self):
        if not self.available:
            raise WarehouseUnavailable()

    async def catalog(self):
        await self.ping()
        return TableCatalog(
            tables=[
                WarehouseTable(
                    name=name,
                    engine="MergeTree",
                    rowCount=len(value["parsed"].rows),
                    columnCount=len(value["parsed"].columns),
                )
                for name, value in sorted(self.tables.items())
            ]
        )

    async def preview(self, table, offset, limit):
        await self.ping()
        if table not in self.tables:
            raise NotFound("Warehouse table")
        parsed = self.tables[table]["parsed"]
        rows = sorted(
            parsed.rows, key=lambda row: tuple((cell is None, cell) for cell in row)
        )
        return TablePreview(
            table=table,
            columns=parsed.columns,
            rows=[
                [ClickHouseWarehouseRepository.cell(value) for value in row]
                for row in rows[offset : offset + limit]
            ],
            total=len(rows),
            offset=offset,
            limit=limit,
        )

    async def import_csv(self, table, parsed, import_id, fingerprint):
        await self.ping()
        if table in self.tables:
            current = self.tables[table]
            if (current["id"], current["fingerprint"]) == (import_id, fingerprint):
                return CsvImportResult(
                    table=table,
                    rowCount=len(parsed.rows),
                    columns=parsed.columns,
                    replayed=True,
                )
            raise Conflict(
                f"A table named '{table}' already exists. Choose another table name."
            )
        self.tables[table] = {
            "parsed": parsed,
            "id": import_id,
            "fingerprint": fingerprint,
        }
        return CsvImportResult(
            table=table, rowCount=len(parsed.rows), columns=parsed.columns
        )

    async def close(self):
        pass
