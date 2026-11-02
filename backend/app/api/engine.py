from fastapi import APIRouter
from ..schemas.common import Identifier


class EngineAPI:
    def __init__(self, service):
        self.service = service
        self.router = APIRouter(
            prefix="/models/{model_id}/revisions/{revision_id}/engine/runs",
            tags=["engine"],
        )
        self.router.add_api_route("/{run_id}", self.status, methods=["GET"])
        self.router.add_api_route("/{run_id}/cancel", self.cancel, methods=["POST"])

    async def status(
        self, model_id: Identifier, revision_id: Identifier, run_id: Identifier
    ):
        return await self.service.run_status(model_id, revision_id, run_id)

    async def cancel(
        self, model_id: Identifier, revision_id: Identifier, run_id: Identifier
    ):
        return await self.service.cancel(model_id, revision_id, run_id)
