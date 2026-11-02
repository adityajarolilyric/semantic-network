from fastapi import APIRouter, Header
from ..schemas.common import Identifier
from ..schemas.planning_view import PlanningViewRequest, PlanningViewEdit


class PlanningViewAPI:
    def __init__(self, service):
        self.service = service
        self.router = APIRouter(
            prefix="/models/{model_id}/revisions/{revision_id}/planning-view",
            tags=["planning-view"],
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
        payload: PlanningViewRequest,
    ):
        return await self.service.query(model_id, revision_id, payload)

    async def edit(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: PlanningViewEdit,
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.service.builds.submit(
            model_id, revision_id, payload.revisionVersion, key, payload
        )
