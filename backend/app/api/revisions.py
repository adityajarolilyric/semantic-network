from fastapi import APIRouter, Depends, Query, Response

from ..core.http import expected_version
from ..schemas.common import Identifier, Page, ValidationReport
from ..schemas.revisions import (
    PublishRevision,
    RevisionCreate,
    RevisionDocument,
    RevisionUpdate,
)
from ..services.revisions import RevisionService
from .base import RevisionResourceAPI


class RevisionAPI:
    def __init__(self, service: RevisionService):
        self.service = service
        self.router = APIRouter(
            prefix="/models/{model_id}/revisions", tags=["revisions"]
        )
        self.router.add_api_route(
            "",
            self.create,
            methods=["POST"],
            response_model=RevisionDocument,
            status_code=201,
        )
        self.router.add_api_route(
            "", self.list, methods=["GET"], response_model=Page[RevisionDocument]
        )
        self.router.add_api_route(
            "/{revision_id}", self.get, methods=["GET"], response_model=RevisionDocument
        )
        self.router.add_api_route(
            "/{revision_id}",
            self.update,
            methods=["PUT"],
            response_model=RevisionDocument,
        )
        self.router.add_api_route(
            "/{revision_id}", self.delete, methods=["DELETE"], status_code=204
        )
        self.router.add_api_route(
            "/{revision_id}/validate",
            self.validate,
            methods=["POST"],
            response_model=ValidationReport,
        )
        self.router.add_api_route(
            "/{revision_id}/publish",
            self.publish,
            methods=["POST"],
            response_model=RevisionDocument,
        )

    async def create(
        self, model_id: Identifier, payload: RevisionCreate, response: Response
    ):
        return RevisionResourceAPI.with_etag(
            response, await self.service.create(model_id, payload)
        )

    async def get(
        self, model_id: Identifier, revision_id: Identifier, response: Response
    ):
        return RevisionResourceAPI.with_etag(
            response, await self.service.get(model_id, revision_id)
        )

    async def list(
        self,
        model_id: Identifier,
        cursor: Identifier | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await self.service.list(model_id, cursor, limit)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: RevisionUpdate,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return RevisionResourceAPI.with_etag(
            response,
            await self.service.update(model_id, revision_id, payload, expected),
        )

    async def validate(self, model_id: Identifier, revision_id: Identifier):
        return await self.service.validate_revision(model_id, revision_id)

    async def publish(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: PublishRevision,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return RevisionResourceAPI.with_etag(
            response,
            await self.service.publish(model_id, revision_id, payload, expected),
        )

    async def delete(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        expected: int = Depends(expected_version),
    ) -> Response:
        await self.service.delete(model_id, revision_id, expected)
        return Response(status_code=204)
