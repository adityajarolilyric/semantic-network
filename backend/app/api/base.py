from fastapi import APIRouter, Depends, Query, Response

from ..core.http import expected_version
from ..schemas.common import Identifier, Page


class RevisionResourceAPI:
    """Shared HTTP mechanics; concrete classes declare typed request contracts."""

    def __init__(self, service, resource: str, document_type):
        self.service = service
        self.router = APIRouter(
            prefix=f"/models/{{model_id}}/revisions/{{revision_id}}/{resource}",
            tags=[resource],
        )
        self.router.add_api_route(
            "",
            self.create,
            methods=["POST"],
            response_model=document_type,
            status_code=201,
        )
        self.router.add_api_route(
            "", self.list, methods=["GET"], response_model=Page[document_type]
        )
        self.router.add_api_route(
            "/{resource_id}", self.get, methods=["GET"], response_model=document_type
        )
        self.router.add_api_route(
            "/{resource_id}", self.update, methods=["PUT"], response_model=document_type
        )
        self.router.add_api_route(
            "/{resource_id}", self.delete, methods=["DELETE"], status_code=204
        )

    @staticmethod
    def with_etag(response, document):
        response.headers["ETag"] = f'"{document["version"]}"'
        return document

    async def create(self, model_id, revision_id, payload, response):
        return self.with_etag(
            response, await self.service.create(model_id, revision_id, payload)
        )

    async def update(
        self, model_id, revision_id, resource_id, payload, response, expected
    ):
        return self.with_etag(
            response,
            await self.service.update(
                model_id, revision_id, resource_id, payload, expected
            ),
        )

    async def get(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        response: Response,
    ):
        return self.with_etag(
            response, await self.service.get(model_id, revision_id, resource_id)
        )

    async def list(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        cursor: Identifier | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await self.service.list(model_id, revision_id, cursor, limit)

    async def delete(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        expected: int = Depends(expected_version),
    ) -> Response:
        await self.service.delete(model_id, revision_id, resource_id, expected)
        return Response(status_code=204)
