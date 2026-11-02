from typing import Literal
from pydantic import BaseModel


from fastapi import APIRouter

from ..core.errors import Conflict
from ..schemas.models import ModelDocument
from ..services.workspace import WorkspaceService


class ResetConfirmation(BaseModel):
    confirmed: Literal[True]


class WorkspaceAPI:
    def __init__(self, service: WorkspaceService, reset_service):
        self.service = service
        self.reset_service = reset_service
        self.router = APIRouter(prefix="/workspace", tags=["workspace"])
        self.router.add_api_route(
            "/bootstrap", self.bootstrap, methods=["POST"], response_model=ModelDocument
        )

        self.router.add_api_route("/reset", self.reset, methods=["POST"])

    async def reset(self, payload: ResetConfirmation):
        return await self.reset_service.reset()

    async def bootstrap(self):
        # A simultaneous first-run insert may hit the default identity's unique index.
        # Retry the complete transaction, which then returns the existing model.
        try:
            return await self.service.bootstrap()
        except Conflict:
            return await self.service.bootstrap()
