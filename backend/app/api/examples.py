from fastapi import Depends, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.examples import ExampleDocument, ExampleInput
from ..services.examples import ExampleService
from .base import RevisionResourceAPI


class ExampleAPI(RevisionResourceAPI):
    def __init__(self, service: ExampleService):
        super().__init__(service, "examples", ExampleDocument)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: ExampleInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: ExampleInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
