from fastapi import APIRouter, Depends, Query, Response

from ..core.http import expected_version
from ..schemas.common import Identifier, Page
from ..schemas.contracts import ContractDocument, ContractInput
from ..services.contracts import ContractService
from .base import RevisionResourceAPI


class ContractAPI:
    def __init__(self, service: ContractService):
        self.service = service
        self.router = APIRouter(prefix="/contracts", tags=["contracts"])
        self.router.add_api_route(
            "",
            self.create,
            methods=["POST"],
            response_model=ContractDocument,
            status_code=201,
        )
        self.router.add_api_route(
            "", self.list, methods=["GET"], response_model=Page[ContractDocument]
        )
        self.router.add_api_route(
            "/{contract_id}", self.get, methods=["GET"], response_model=ContractDocument
        )
        self.router.add_api_route(
            "/{contract_id}",
            self.update,
            methods=["PUT"],
            response_model=ContractDocument,
        )
        self.router.add_api_route(
            "/{contract_id}", self.delete, methods=["DELETE"], status_code=204
        )

    async def create(self, payload: ContractInput, response: Response):
        return RevisionResourceAPI.with_etag(
            response, await self.service.create(payload)
        )

    async def get(self, contract_id: Identifier, response: Response):
        return RevisionResourceAPI.with_etag(
            response, await self.service.get(contract_id)
        )

    async def list(
        self,
        cursor: Identifier | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ):
        return await self.service.list(cursor, limit)

    async def update(
        self,
        contract_id: Identifier,
        payload: ContractInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return RevisionResourceAPI.with_etag(
            response, await self.service.update(contract_id, payload, expected)
        )

    async def delete(
        self, contract_id: Identifier, expected: int = Depends(expected_version)
    ) -> Response:
        await self.service.delete(contract_id, expected)
        return Response(status_code=204)
