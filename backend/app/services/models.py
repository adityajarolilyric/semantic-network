from ..core.errors import Conflict
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ModelLookup, RevisionLookup
from .base import Service, now


class ModelService(Service):
    async def create(self, payload):
        async def operation(repo):
            timestamp = now()
            document = {
                **payload.model_dump(),
                "version": 1,
                "createdAt": timestamp,
                "updatedAt": timestamp,
                "draftRevisionId": None,
                "publishedRevisionId": None,
                "revisionCounter": 0,
            }
            return await metadata_repositories(repo).models.insert(document)

        return await self.store.transaction(operation)

    async def get(self, model_id):
        return await metadata_repositories(self.store.repository).models.require(
            ModelLookup(model_id=model_id), "Model"
        )

    async def list(self, cursor, limit):
        return await metadata_repositories(self.store.repository).models.page(
            ModelLookup(), cursor, limit
        )

    async def update(self, model_id, payload, expected):
        async def operation(repo):
            document = await metadata_repositories(repo).models.require(
                ModelLookup(model_id=model_id), "Model"
            )
            self.check_version(document, expected)
            updated = {
                **document,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            if not await metadata_repositories(repo).models.replace(
                ModelLookup(model_id=model_id, version=expected), updated
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, model_id, expected):
        async def operation(repo):
            document = await metadata_repositories(repo).models.require(
                ModelLookup(model_id=model_id), "Model"
            )
            self.check_version(document, expected)
            await metadata_repositories(repo).revisions.ensure_empty(
                RevisionLookup(model_id=model_id),
                "Delete draft revisions before deleting their model; published history cannot be deleted.",
            )
            if not await metadata_repositories(repo).models.delete(
                ModelLookup(model_id=model_id, version=expected)
            ):
                raise Conflict()

        await self.store.transaction(operation)
