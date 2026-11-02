from fastapi import Depends, Response, Header

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.measures import MeasureDocument, MeasureInput
from ..services.measures import MeasureService
from .base import RevisionResourceAPI


class MeasureAPI(RevisionResourceAPI):
    def __init__(self, service: MeasureService, builds=None):
        super().__init__(service, "measures", MeasureDocument)
        self.builds = builds
        self.router.add_api_route(
            "/execution/build", self.build_values, methods=["POST"], status_code=202
        )
        self.router.add_api_route(
            "/execution/levels/{level_id}", self.values, methods=["GET"]
        )
        self.router.add_api_route(
            "/{resource_id}/capabilities", self.capabilities, methods=["GET"]
        )

    async def build_values(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        expected: int = Depends(expected_version),
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.builds.submit(model_id, revision_id, expected, key)

    async def values(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        level_id: Identifier,
        preview: bool = False,
    ):
        return await self.builds.status(model_id, revision_id, level_id, preview)

    async def capabilities(
        self, model_id: Identifier, revision_id: Identifier, resource_id: Identifier
    ):
        return await self.builds.capabilities(model_id, revision_id, resource_id)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: MeasureInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: MeasureInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
