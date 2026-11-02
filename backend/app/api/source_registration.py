from fastapi import APIRouter, Response

from ..schemas.common import Identifier
from ..schemas.sources import SourceDocument, SourceRegistration
from ..services.source_registration import SourceRegistrationService
from .base import RevisionResourceAPI


class SourceRegistrationAPI:
    def __init__(self, service: SourceRegistrationService):
        self.service = service
        self.router = APIRouter(
            prefix="/models/{model_id}/revisions/{revision_id}/source-registrations",
            tags=["sources"],
        )
        self.router.add_api_route(
            "",
            self.register,
            methods=["POST"],
            response_model=SourceDocument,
            status_code=201,
        )

    async def register(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: SourceRegistration,
        response: Response,
    ):
        return RevisionResourceAPI.with_etag(
            response, await self.service.register(model_id, revision_id, payload)
        )
