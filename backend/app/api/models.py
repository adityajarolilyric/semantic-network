from fastapi import APIRouter, Depends, Query, Response

from ..core.http import expected_version
from ..schemas.common import Identifier, Page
from ..schemas.models import ModelCreate, ModelDocument, ModelUpdate
from ..services.models import ModelService
from .base import RevisionResourceAPI


class ModelAPI:
    def __init__(self, service: ModelService):
        self.service = service
        self.router = APIRouter(prefix="/models", tags=["models"])
        self.router.add_api_route(
            "",
            self.create,
            methods=["POST"],
            response_model=ModelDocument,
            status_code=201,
        )
        self.router.add_api_route(
            "", self.list, methods=["GET"], response_model=Page[ModelDocument]
        )
        self.router.add_api_route(
            "/{model_id}", self.get, methods=["GET"], response_model=ModelDocument
        )
        self.router.add_api_route(
            "/{model_id}", self.update, methods=["PUT"], response_model=ModelDocument
        )
        self.router.add_api_route(
            "/{model_id}", self.delete, methods=["DELETE"], status_code=204
        )

    async def create(self, payload: ModelCreate, response: Response):
        return RevisionResourceAPI.with_etag(
            response, await self.service.create(payload)
        )

    async def get(self, model_id: Identifier, response: Response):
        return RevisionResourceAPI.with_etag(response, await self.service.get(model_id))

    async def list(
        self,
        cursor: Identifier | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await self.service.list(cursor, limit)

    async def update(
        self,
        model_id: Identifier,
        payload: ModelUpdate,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return RevisionResourceAPI.with_etag(
            response, await self.service.update(model_id, payload, expected)
        )

    async def delete(
        self, model_id: Identifier, expected: int = Depends(expected_version)
    ) -> Response:
        await self.service.delete(model_id, expected)
        return Response(status_code=204)
