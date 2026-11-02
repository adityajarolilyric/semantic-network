from fastapi import Depends, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.evidence import EvidenceDocument, EvidenceInput
from ..services.evidence import EvidenceService
from .base import RevisionResourceAPI


class EvidenceAPI(RevisionResourceAPI):
    def __init__(self, service: EvidenceService):
        super().__init__(service, "evidence", EvidenceDocument)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: EvidenceInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: EvidenceInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
