from fastapi import Depends, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.sources import SourceDocument, SourceInput
from ..services.sources import SourceService
from .base import RevisionResourceAPI


class SourceAPI(RevisionResourceAPI):
    def __init__(self, service: SourceService):
        super().__init__(service, "sources", SourceDocument)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: SourceInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: SourceInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
