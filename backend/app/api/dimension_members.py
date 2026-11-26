from fastapi import Depends, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.dimension_members import (
    DimensionMembersCreateInput,
    DimensionMembersDocument,
    DimensionMembersInput,
)
from ..schemas.managed_tables import ObjectInputTransform
from ..services.dimension_members import DimensionMembersService
from .base import RevisionResourceAPI


class DimensionMembersAPI(RevisionResourceAPI):
    def __init__(self, service: DimensionMembersService, inputs=None):
        super().__init__(service, "dimension-members", DimensionMembersDocument)
        self.inputs = inputs
        self.router.add_api_route(
            "/{resource_id}/preview", self.saved_preview, methods=["GET"]
        )
        self.router.add_api_route("/{resource_id}/grain", self.grain, methods=["POST"])

        self.router.add_api_route("/input/schema", self.input_schema, methods=["POST"])
        self.router.add_api_route(
            "/input/preview", self.input_preview, methods=["POST"]
        )
        self.router.add_api_route("/preview", self.preview, methods=["POST"])

    async def saved_preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        sql_only: bool = False,
    ):
        owner = await self.service.owner(model_id, revision_id, resource_id)
        return await self.service.dimensions.preview_saved(
            model_id, revision_id, owner["objectId"], sql_only
        )

    async def grain(
        self, model_id: Identifier, revision_id: Identifier, resource_id: Identifier
    ):
        return await self.service.ensure_grain(model_id, revision_id, resource_id)

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
        sql_only: bool = False,
    ):
        return await self.inputs.preview_input(
            model_id, revision_id, payload, sql_only=sql_only
        )

    async def preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionMembersInput,
        sql_only: bool = False,
    ):
        return await self.inputs.preview_dimension(
            model_id, revision_id, payload, sql_only=sql_only
        )

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionMembersCreateInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: DimensionMembersInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
