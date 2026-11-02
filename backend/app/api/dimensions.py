from fastapi import Depends, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.dimensions import DimensionDocument, DimensionInput
from ..services.dimensions import DimensionService
from .base import RevisionResourceAPI


class DimensionAPI(RevisionResourceAPI):
    def __init__(self, service: DimensionService):
        super().__init__(service, "dimensions", DimensionDocument)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: DimensionInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
