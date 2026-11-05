"""Administrative fixture routes for historical multi-revision service tests only.

Production create_app never installs these. They let isolated tests seed custom
scopes and exercise retained lifecycle services without restoring public APIs.
"""

from fastapi import APIRouter, Depends, Response

from backend.app.main import create_app as production_app
from backend.app.core.http import expected_version
from backend.app.schemas.common import Identifier
from backend.app.schemas.models import ModelCreate
from backend.app.schemas.revisions import RevisionCreate, PublishRevision
from backend.app.api.base import RevisionResourceAPI


def create_fixture_app(*args, **kwargs):
    app = production_app(*args, **kwargs)
    router = APIRouter(prefix="/api/v1/models", include_in_schema=False)

    @router.post("", status_code=201)
    async def create_model(payload: ModelCreate, response: Response):
        return RevisionResourceAPI.with_etag(
            response, await app.state.container.models.create(payload)
        )

    @router.post("/{model_id}/revisions", status_code=201)
    async def create_revision(
        model_id: Identifier, payload: RevisionCreate, response: Response
    ):
        return RevisionResourceAPI.with_etag(
            response, await app.state.container.revisions.create(model_id, payload)
        )

    @router.post("/{model_id}/revisions/{revision_id}/publish")
    async def publish(
        model_id: Identifier,
        revision_id: Identifier,
        payload: PublishRevision,
        response: Response,
        expected: int = Depends(expected_version),
    ):
        return RevisionResourceAPI.with_etag(
            response,
            await app.state.container.revisions.publish(
                model_id, revision_id, payload, expected
            ),
        )

    @router.delete("/{model_id}", status_code=204)
    async def delete_model(
        model_id: Identifier, expected: int = Depends(expected_version)
    ):
        await app.state.container.models.delete(model_id, expected)
        return Response(status_code=204)

    @router.delete("/{model_id}/revisions/{revision_id}", status_code=204)
    async def delete_revision(
        model_id: Identifier,
        revision_id: Identifier,
        expected: int = Depends(expected_version),
    ):
        await app.state.container.revisions.delete(model_id, revision_id, expected)
        return Response(status_code=204)

    app.include_router(router)
    return app
