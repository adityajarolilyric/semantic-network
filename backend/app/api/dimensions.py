from fastapi import Depends, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.managed_tables import ObjectInputTransform
from ..schemas.dimensions import DimensionDocument, DimensionInput
from ..services.dimensions import DimensionService
from .base import RevisionResourceAPI


class DimensionAPI(RevisionResourceAPI):
    def __init__(self, service: DimensionService, inputs=None):
        super().__init__(service, "dimensions", DimensionDocument)
        self.inputs = inputs
        self.router.add_api_route("/input/schema", self.input_schema, methods=["POST"])
        self.router.add_api_route(
            "/input/preview", self.input_preview, methods=["POST"]
        )
        self.router.add_api_route("/preview", self.preview, methods=["POST"])

    async def input_schema(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: ObjectInputTransform,
    ):
        return await self.inputs.input_schema(model_id, revision_id, payload)

    async def input_preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: ObjectInputTransform,
    ):
        return await self.inputs.preview(
            model_id, revision_id, payload, input_only=True
        )

    async def preview(
        self, model_id: Identifier, revision_id: Identifier, payload: DimensionInput
    ):
        return await self.inputs.preview(
            model_id, revision_id, payload, dimension_only=True
        )

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
