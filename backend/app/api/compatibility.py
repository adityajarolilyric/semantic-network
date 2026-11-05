from fastapi import APIRouter
from ..schemas.common import Identifier
from ..schemas.compatibility import CompatibilityRequest


class CompatibilityAPI:
    def __init__(self, service):
        self.service = service
        self.router = APIRouter(
            prefix="/models/{model_id}/revisions/{revision_id}/compatibility",
            tags=["compatibility"],
        )
        self.router.add_api_route("", self.graph, methods=["POST"])

    async def graph(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: CompatibilityRequest,
    ):
        return await self.service.graph(model_id, revision_id, payload)
