from fastapi import Depends, Header, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.dimension_members import DimensionMembersInput
from ..schemas.dimensions import (
    DimensionCommitRequest,
    DimensionDocument,
    DimensionDraftRequest,
    DimensionInput,
)
from ..services.dimensions import DimensionService
from .base import RevisionResourceAPI


class DimensionAPI(RevisionResourceAPI):
    def __init__(self, service: DimensionService, builds=None, commands=None):
        super().__init__(service, "dimensions", DimensionDocument)
        self.builds = builds
        self.commands = commands
        self.router.add_api_route(
            "/{resource_id}/preview", self.saved_preview, methods=["GET"]
        )
        self.router.add_api_route(
            "/save-and-build", self.save_and_build, methods=["POST"], status_code=202
        )
        self.router.add_api_route("/schema", self.output_schema, methods=["POST"])
        self.router.add_api_route(
            "/{resource_id}/build", self.build_table, methods=["POST"], status_code=202
        )
        self.router.add_api_route("/{resource_id}/table", self.table, methods=["GET"])
        self.router.add_api_route(
            "/editor/draft/build", self.build_draft, methods=["POST"], status_code=202
        )
        self.router.add_api_route(
            "/editor/draft/{run_id}/preview", self.draft_preview, methods=["GET"]
        )
        self.router.add_api_route(
            "/editor/draft/commit",
            self.commit_draft,
            methods=["POST"],
            response_model=DimensionDocument,
        )
        self.router.add_api_route(
            "/preview/population", self.preview_population, methods=["POST"]
        )

    async def saved_preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        sql_only: bool = False,
    ):
        return await self.service.preview_saved(
            model_id, revision_id, resource_id, sql_only
        )

    async def output_schema(
        self, model_id: Identifier, revision_id: Identifier, payload: DimensionInput
    ):
        return await self.builds.schema(model_id, revision_id, payload)

    async def save_and_build(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionDraftRequest,
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.commands.save_and_build(model_id, revision_id, payload, key)

    async def build_draft(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionDraftRequest,
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.builds.submit(
            model_id,
            revision_id,
            payload.definition.objectId,
            payload.expectedVersion,
            key,
            payload.definition,
        )

    async def draft_preview(
        self, model_id: Identifier, revision_id: Identifier, run_id: Identifier
    ):
        return await self.builds.draft_preview(model_id, revision_id, run_id)

    async def commit_draft(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionCommitRequest,
    ):
        return await self.builds.commit_draft(
            model_id, revision_id, payload.runId, payload.definition
        )

    async def build_table(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        expected: int = Depends(expected_version),
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.builds.submit(
            model_id, revision_id, resource_id, expected, key
        )

    async def table(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        preview: bool = False,
    ):
        return await self.builds.status(
            model_id, revision_id, resource_id, preview=preview
        )

    async def preview_population(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionInput,
        sql_only: bool = False,
    ):
        return await self.builds.preview(
            model_id, revision_id, payload, sql_only=sql_only
        )

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: DimensionInput | DimensionMembersInput,
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
