from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..core.errors import MetadataError
from ..schemas.common import Identifier
from ..warehouse.schemas import (
    CsvImportResult,
    TableCatalog,
    TablePreview,
    WarehouseStatus,
)
from ..warehouse.service import WarehouseSourceService


class WarehouseSourcesAPI:
    """One physical-source context: connection status, catalog, CSV creation, preview."""

    def __init__(self, service: WarehouseSourceService):
        self.service = service
        self.router = APIRouter(prefix="/warehouse", tags=["warehouse-sources"])
        self.router.add_api_route(
            "/status", self.status, methods=["GET"], response_model=WarehouseStatus
        )
        self.router.add_api_route(
            "/tables", self.catalog, methods=["GET"], response_model=TableCatalog
        )
        self.router.add_api_route(
            "/tables/{table}/preview",
            self.preview,
            methods=["GET"],
            response_model=TablePreview,
        )
        self.router.add_api_route(
            "/imports/csv",
            self.import_csv,
            methods=["POST"],
            response_model=CsvImportResult,
            status_code=201,
        )

    async def status(self):
        return await self.service.status()

    async def catalog(self):
        return await self.service.catalog()

    async def preview(
        self,
        table: str,
        offset: int = Query(default=0, ge=0, le=1_000_000),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        return await self.service.preview(table, offset, limit)

    async def import_csv(
        self,
        request: Request,
        tableName: Annotated[str, Form(min_length=1, max_length=128)],
        file: Annotated[UploadFile, File()],
        import_id: Annotated[Identifier, Header(alias="Idempotency-Key")],
    ):
        form = await request.form()
        try:
            uploads = [
                value
                for _, value in form.multi_items()
                if isinstance(value, StarletteUploadFile)
            ]
            if (
                len(uploads) != 1
                or len(form.getlist("file")) != 1
                or len(form.getlist("tableName")) != 1
                or set(form) != {"file", "tableName"}
            ):
                raise MetadataError(
                    "invalid_upload",
                    "Upload exactly one CSV file and its table name per request.",
                )
            content = await file.read(self.service.settings.csv_max_file_bytes + 1)
            return await self.service.import_csv(
                tableName, file.filename, content, import_id
            )
        finally:
            await form.close()
