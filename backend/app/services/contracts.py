from ..core.errors import Conflict, MetadataError
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ContractLookup
from .base import Service, now


class ContractService(Service):
    """Store documentation contracts; they do not replace server-side validators."""

    async def create(self, payload):
        async def operation(repo):
            timestamp = now()
            return await metadata_repositories(repo).contracts.insert(
                {
                    **payload.model_dump(),
                    "version": 1,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                }
            )

        return await self.store.transaction(operation)

    async def get(self, contract_id):
        return await metadata_repositories(self.store.repository).contracts.require(
            ContractLookup(contract_id=contract_id), "Contract"
        )

    async def list(self, cursor, limit):
        return await metadata_repositories(self.store.repository).contracts.page(
            ContractLookup(), cursor, limit
        )

    async def update(self, contract_id, payload, expected):
        if payload.contractId != contract_id:
            raise MetadataError(
                "identity_mismatch", "contractId cannot change during an update."
            )

        async def operation(repo):
            document = await metadata_repositories(repo).contracts.require(
                ContractLookup(contract_id=contract_id), "Contract"
            )
            self.check_version(document, expected)
            updated = {
                **document,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            if not await metadata_repositories(repo).contracts.replace(
                ContractLookup(contract_id=contract_id, version=expected), updated
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, contract_id, expected):
        async def operation(repo):
            document = await metadata_repositories(repo).contracts.require(
                ContractLookup(contract_id=contract_id), "Contract"
            )
            self.check_version(document, expected)
            if not await metadata_repositories(repo).contracts.delete(
                ContractLookup(contract_id=contract_id, version=expected)
            ):
                raise Conflict()

        await self.store.transaction(operation)
