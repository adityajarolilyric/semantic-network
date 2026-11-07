from fastapi import APIRouter, Header

from ..schemas.common import Identifier
from ..schemas.decision_view import DecisionViewEdit, DecisionViewRequest


class DecisionViewAPI:
    def __init__(self, service):
        self.service = service
        self.router = APIRouter(
            prefix="/models/{model_id}/revisions/{revision_id}/decision-view",
            tags=["decision-view"],
        )
        self.router.add_api_route("/options", self.options, methods=["GET"])
        self.router.add_api_route("/query", self.query, methods=["POST"])
        self.router.add_api_route(
            "/edits", self.edit, methods=["POST"], status_code=202
        )

    async def options(self, model_id: Identifier, revision_id: Identifier):
        return await self.service.options(model_id, revision_id)

    async def query(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DecisionViewRequest,
    ):
        return await self.service.query(model_id, revision_id, payload)

    async def edit(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DecisionViewEdit,
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.service.builds.submit(
            model_id, revision_id, payload.revisionVersion, key, payload
        )
