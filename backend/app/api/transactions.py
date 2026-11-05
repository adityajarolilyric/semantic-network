from fastapi import Depends, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.managed_tables import ObjectInputTransform
from ..schemas.transactions import (
    CalendarSchemaInput,
    TransactionDocument,
    TransactionCreateInput,
    TransactionFromGrainInput,
    TransactionInput,
)
from ..services.transactions import TransactionService
from .base import RevisionResourceAPI


class TransactionAPI(RevisionResourceAPI):
    def __init__(self, service: TransactionService, resolution=None, grains=None):
        super().__init__(service, "transactions", TransactionDocument)
        self.resolution = resolution
        self.grains = grains
        self.router.add_api_route(
            "/from-grain", self.from_grain, methods=["POST"], status_code=201
        )
        self.router.add_api_route(
            "/from-grain/preview", self.preview_grain, methods=["POST"]
        )
        self.router.add_api_route("/input/schema", self.input_schema, methods=["POST"])
        self.router.add_api_route(
            "/input/preview", self.input_preview, methods=["POST"]
        )
        self.router.add_api_route(
            "/calendar/schema", self.calendar_schema, methods=["POST"]
        )
        self.router.add_api_route(
            "/calendar/preview", self.calendar_preview, methods=["POST"]
        )
        self.router.add_api_route("/resolution/options", self.options, methods=["GET"])
        self.router.add_api_route("/resolution/preview", self.preview, methods=["POST"])
        self.router.add_api_route(
            "/resolution/schema", self.output_schema, methods=["POST"]
        )
        self.router.add_api_route("/{resource_id}/schema", self.schema, methods=["GET"])

    async def from_grain(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: TransactionFromGrainInput,
    ):
        return await self.grains.create(model_id, revision_id, payload)

    async def preview_grain(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: TransactionFromGrainInput,
    ):
        return await self.grains.preview(model_id, revision_id, payload)

    async def output_schema(
        self, model_id: Identifier, revision_id: Identifier, payload: TransactionInput
    ):
        return await self.resolution.schema(model_id, revision_id, payload=payload)

    async def input_schema(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: ObjectInputTransform,
    ):
        return await self.resolution.input_schema(model_id, revision_id, payload)

    async def input_preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: ObjectInputTransform,
    ):
        return await self.resolution.preview_input(model_id, revision_id, payload)

    async def calendar_schema(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: CalendarSchemaInput,
    ):
        return await self.resolution.calendar_schema(model_id, revision_id, payload)

    async def calendar_preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: CalendarSchemaInput,
    ):
        return await self.resolution.preview_calendar(model_id, revision_id, payload)

    async def options(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        planningLevelId: Identifier | None = None,
    ):
        return await self.resolution.options(model_id, revision_id, planningLevelId)

    async def preview(
        self, model_id: Identifier, revision_id: Identifier, payload: TransactionInput
    ):
        return await self.resolution.preview(model_id, revision_id, payload)

    async def schema(
        self, model_id: Identifier, revision_id: Identifier, resource_id: Identifier
    ):
        return await self.resolution.schema(model_id, revision_id, resource_id)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: TransactionCreateInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: TransactionInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
