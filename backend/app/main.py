from .api.planning_view import PlanningViewAPI
from .core.workspace_middleware import WorkspaceMiddleware
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from .api.engine import EngineAPI
from .api.managed_tables import ManagedTablesAPI
from .api.contracts import ContractAPI
from .api.dimensions import DimensionAPI
from .api.evidence import EvidenceAPI
from .api.examples import ExampleAPI
from .api.health import HealthAPI
from .api.measures import MeasureAPI
from .api.models import ModelAPI
from .api.planning_levels import PlanningLevelAPI
from .api.revisions import RevisionAPI
from .api.source_registration import SourceRegistrationAPI
from .api.sources import SourceAPI
from .api.transactions import TransactionAPI
from .api.warehouse_sources import WarehouseSourcesAPI
from .api.workspace import WorkspaceAPI
from .container import ServiceContainer
from .core.config import Settings
from .core.errors import MetadataError
from .core.http import RequestMiddleware, error_payload
from .core.logging import configure_logging
from .repositories.base import MetadataStore
from .repositories.mongo import MongoMetadataStore
from .warehouse.repository import ClickHouseWarehouseRepository, WarehouseRepository

logger = logging.getLogger("metadata")


class ExceptionHandlers:
    @staticmethod
    async def domain(request, exc: MetadataError):
        return JSONResponse(
            error_payload(request, exc.code, exc.message, exc.details),
            status_code=exc.status,
        )

    @staticmethod
    async def validation(request, exc: RequestValidationError):
        details = [
            {"path": list(e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            error_payload(
                request, "invalid_request", "Request validation failed.", details
            ),
            status_code=422,
        )

    @staticmethod
    async def database(request, exc: PyMongoError):
        logger.error(
            "MongoDB request failed",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            error_payload(
                request,
                "database_unavailable",
                "Metadata storage is temporarily unavailable.",
            ),
            status_code=503,
        )

    @staticmethod
    async def http(request, exc: HTTPException):
        return JSONResponse(
            error_payload(request, "http_error", str(exc.detail)),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @staticmethod
    async def unexpected(request, exc: Exception):
        logger.exception(
            "Unexpected metadata request failure",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(
            error_payload(request, "internal_error", "An unexpected error occurred."),
            status_code=500,
        )


class ApplicationFactory:
    def __init__(
        self, settings: Settings, store: MetadataStore, warehouse: WarehouseRepository
    ):
        self.settings = settings
        self.store = store
        self.warehouse = warehouse
        self.container = ServiceContainer(store, settings, warehouse)

    @asynccontextmanager
    async def lifespan(self, app):
        try:
            await self.store.open()
            self.container.engine_worker.start()
            yield
        finally:
            await self.container.engine_worker.stop()
            try:
                await self.warehouse.close()
            finally:
                await self.store.close()

    def build(self) -> FastAPI:
        configure_logging()
        app = FastAPI(
            title=self.settings.app_name, version="1.0.0", lifespan=self.lifespan
        )
        app.state.container = self.container
        app.add_exception_handler(MetadataError, ExceptionHandlers.domain)
        app.add_exception_handler(RequestValidationError, ExceptionHandlers.validation)
        app.add_exception_handler(PyMongoError, ExceptionHandlers.database)
        app.add_exception_handler(HTTPException, ExceptionHandlers.http)
        app.add_exception_handler(Exception, ExceptionHandlers.unexpected)
        app.add_middleware(WorkspaceMiddleware, gate=self.container.workspace_gate)
        app.add_middleware(RequestMiddleware, settings=self.settings)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=self.settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "Authorization",
                "If-Match",
                "Idempotency-Key",
            ],
            expose_headers=["ETag", "X-Request-ID"],
        )
        container = self.container
        for api in (
            ModelAPI(container.models),
            WorkspaceAPI(container.workspace, container.workspace_reset),
            RevisionAPI(container.revisions),
            SourceAPI(container.sources),
            ManagedTablesAPI(container.managed_tables),
            EngineAPI(container.managed_tables),
            SourceRegistrationAPI(container.source_registration),
            DimensionAPI(container.dimensions, container.transaction_resolution),
            PlanningLevelAPI(container.planning_levels, container.planning_builds),
            TransactionAPI(container.transactions, container.transaction_resolution),
            MeasureAPI(container.measures, container.measure_builds),
            PlanningViewAPI(container.planning_view),
            EvidenceAPI(container.evidence),
            ExampleAPI(container.examples),
            ContractAPI(container.contracts),
            WarehouseSourcesAPI(container.warehouse_sources),
        ):
            app.include_router(api.router, prefix="/api/v1")
        app.include_router(HealthAPI(self.store).router)
        return app


def create_app(
    settings: Settings | None = None,
    store: MetadataStore | None = None,
    warehouse: WarehouseRepository | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if settings.environment == "production" and not settings.api_key:
        raise ValueError(
            "METADATA_API_KEY is required in production; use an authenticated ingress for user-level authorization."
        )
    return ApplicationFactory(
        settings,
        store if store is not None else MongoMetadataStore(settings),
        warehouse if warehouse is not None else ClickHouseWarehouseRepository(settings),
    ).build()
