"""Compatibility command for Transaction-first authoring; never creates Dimensions."""

from ..schemas.transactions import (
    TransactionCreateInput,
    TransactionDocument,
    TransactionInput,
)


class TransactionGrainService:
    def __init__(self, transactions, resolution):
        self.transactions, self.resolution = transactions, resolution

    @staticmethod
    def transaction_input(payload):
        return TransactionCreateInput.model_validate(
            {
                "objectId": payload.objectId,
                "name": payload.name,
                "displayName": payload.displayName,
                "description": payload.description,
                "measures": [m.model_dump() for m in payload.measures],
                "definition": {
                    "sourceId": payload.sourceId,
                    "grainColumnIds": payload.grainColumnIds,
                    "pivot": payload.pivot.model_dump() if payload.pivot else None,
                    "calendarResolution": payload.calendarResolution.model_dump()
                    if payload.calendarResolution
                    else None,
                    "filters": payload.filters.model_dump()
                    if payload.filters
                    else None,
                },
            }
        )

    async def create(self, model, revision, payload):
        document = await self.transactions.create(
            model, revision, self.transaction_input(payload), allow_existing=True
        )
        return {
            "transaction": TransactionDocument.model_validate(document).model_dump()
        }

    async def preview(self, model, revision, payload, *, sql_only=False):
        command = self.transaction_input(payload)
        candidate = TransactionInput.model_validate(
            command.model_dump(exclude={"measures"})
        )
        return await self.resolution.preview(
            model, revision, candidate, sql_only=sql_only
        )
