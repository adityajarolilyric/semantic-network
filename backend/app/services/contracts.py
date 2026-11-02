from ..core.errors import Conflict, MetadataError
from .base import Service, now


class ContractService(Service):
    """Store documentation contracts; they do not replace server-side validators."""

    async def create(self, payload):
        async def operation(repo):
            timestamp = now()
            return await repo.insert(
                "semantic_contracts",
                {
                    **payload.model_dump(),
                    "version": 1,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                },
            )

        return await self.store.transaction(operation)

    async def get(self, contract_id):
        return await self.require(
            self.store.repository,
            "semantic_contracts",
            {"contractId": contract_id},
            "Contract",
        )

    async def list(self, cursor, limit):
        return await self.page("semantic_contracts", {}, "contractId", cursor, limit)

    async def update(self, contract_id, payload, expected):
        if payload.contractId != contract_id:
            raise MetadataError(
                "identity_mismatch", "contractId cannot change during an update."
            )

        async def operation(repo):
            query = {"contractId": contract_id}
            document = await self.require(repo, "semantic_contracts", query, "Contract")
            self.check_version(document, expected)
            updated = {
                **document,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            if not await repo.replace(
                "semantic_contracts", {**query, "version": expected}, updated
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, contract_id, expected):
        async def operation(repo):
            query = {"contractId": contract_id}
            document = await self.require(repo, "semantic_contracts", query, "Contract")
            self.check_version(document, expected)
            if not await repo.delete(
                "semantic_contracts", {**query, "version": expected}
            ):
                raise Conflict()

        await self.store.transaction(operation)
