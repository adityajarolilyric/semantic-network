from fastapi import Depends, Response, Header

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.planning_levels import (
    PlanningLevelDocument,
    PlanningLevelInput,
    PlanningLevelDraftRequest,
    PlanningLevelCommitRequest,
)
from ..services.planning_levels import PlanningLevelService
from .base import RevisionResourceAPI


class PlanningLevelAPI(RevisionResourceAPI):
    def __init__(self, service: PlanningLevelService, builds=None):
        super().__init__(service, "planning-levels", PlanningLevelDocument)
        self.builds = builds
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
            response_model=PlanningLevelDocument,
        )
        self.router.add_api_route(
            "/preview/population", self.preview_population, methods=["POST"]
        )

    async def output_schema(
        self, model_id: Identifier, revision_id: Identifier, payload: PlanningLevelInput
    ):
        return await self.builds.schema(model_id, revision_id, payload)

    async def build_draft(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: PlanningLevelDraftRequest,
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
        payload: PlanningLevelCommitRequest,
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
        self, model_id: Identifier, revision_id: Identifier, payload: PlanningLevelInput
    ):
        return await self.builds.preview(model_id, revision_id, payload)

    async def create(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: PlanningLevelInput,
        response: Response,
    ):
        return await super().create(model_id, revision_id, payload, response)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        resource_id: Identifier,
        payload: PlanningLevelInput,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return await super().update(
            model_id, revision_id, resource_id, payload, response, expected
        )
