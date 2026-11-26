from fastapi import APIRouter, Depends, Header, Query, Response

from ..core.http import expected_version
from ..schemas.common import Identifier
from ..schemas.managed_tables import ManagedDefinition, ManagedEnable
from ..schemas.pivot_suggestions import PivotSuggestionInput, PivotSuggestionResult


class ManagedTablesAPI:
    def __init__(self, service, suggestions=None):
        self.service = service
        self.suggestions = suggestions
        self.router = APIRouter(
            prefix="/models/{model_id}/revisions/{revision_id}/managed-tables",
            tags=["managed-tables"],
        )
        self.router.add_api_route(
            "/pivot-suggestion",
            self.suggest_pivot,
            methods=["POST"],
            response_model=PivotSuggestionResult,
        )
        for path, method, handler, status in [
            ("", "GET", self.list, 200),
            ("/enable", "POST", self.enable, 202),
            ("/upgrade-contract", "POST", self.upgrade, 200),
            ("/{source_id}", "GET", self.get, 200),
            ("/{source_id}", "PUT", self.update, 202),
            ("/{source_id}/disable", "POST", self.disable, 204),
            ("/{source_id}/attribute-values", "GET", self.values, 200),
            ("/{source_id}/schema", "POST", self.schema, 200),
            ("/{source_id}/preview", "GET", self.preview, 200),
            ("/{source_id}/preview", "POST", self.proposed_preview, 200),
            ("/{source_id}/ensure-view", "POST", self.repair, 202),
        ]:
            self.router.add_api_route(
                path, handler, methods=[method], status_code=status
            )

    async def list(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        cursor: Identifier | None = None,
        limit: int = Query(50, ge=1, le=100),
    ):
        return await self.service.list(model_id, revision_id, cursor, limit)

    async def suggest_pivot(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: PivotSuggestionInput,
    ):
        return await self.suggestions.suggest(model_id, revision_id, payload.tableId)

    async def get(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        response: Response,
    ):
        document = await self.service.get(model_id, revision_id, source_id)
        response.headers["ETag"] = f'"{document["version"]}"'
        return document

    async def enable(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        payload: ManagedEnable,
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.service.enable(model_id, revision_id, payload.tableName, key)

    async def update(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        payload: ManagedDefinition,
        expected: int = Depends(expected_version),
        key: str = Header(alias="Idempotency-Key"),
    ):
        return await self.service.submit(
            model_id, revision_id, source_id, payload, expected, key
        )

    async def disable(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        expected: int = Depends(expected_version),
    ):
        await self.service.disable(model_id, revision_id, source_id, expected)
        return Response(status_code=204)

    async def values(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        columnId: Identifier,
        managedOutput: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
    ):
        if managedOutput:
            return await self.service.output_attribute_values(
                model_id, revision_id, source_id, columnId, offset, limit
            )
        source = await self.service.source(
            self.service.store.repository, model_id, revision_id, source_id
        )
        return await self.service.executor.attribute_values(
            source, columnId, offset, limit
        )

    async def preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        sql_only: bool = False,
    ):
        return await self.service.preview(
            model_id, revision_id, source_id, sql_only=sql_only
        )

    async def schema(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        payload: ManagedDefinition,
    ):
        return await self.service.schema(model_id, revision_id, source_id, payload)

    async def proposed_preview(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        payload: ManagedDefinition,
        sql_only: bool = False,
    ):
        return await self.service.preview(
            model_id, revision_id, source_id, payload, sql_only=sql_only
        )

    async def repair(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        source_id: Identifier,
        key: str = Header(alias="Idempotency-Key"),
    ):
        current = await self.service.get(model_id, revision_id, source_id)
        return await self.service.submit(
            model_id,
            revision_id,
            source_id,
            self.service.input_definition(current["definition"]),
            current["version"],
            key,
            repair=True,
        )

    async def upgrade(
        self,
        model_id: Identifier,
        revision_id: Identifier,
        expected: int = Depends(expected_version),
    ):
        return await self.service.upgrade_contract(model_id, revision_id, expected)
