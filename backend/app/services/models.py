from .base import Service, now
from ..core.errors import Conflict


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
            return await repo.insert("semantic_models", document)

        return await self.store.transaction(operation)

    async def get(self, model_id):
        return await self.require(
            self.store.repository, "semantic_models", {"modelId": model_id}, "Model"
        )

    async def list(self, cursor, limit):
        return await self.page("semantic_models", {}, "modelId", cursor, limit)

    async def update(self, model_id, payload, expected):
        async def operation(repo):
            query = {"modelId": model_id}
            document = await self.require(repo, "semantic_models", query, "Model")
            self.check_version(document, expected)
            updated = {
                **document,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            if not await repo.replace(
                "semantic_models", {**query, "version": expected}, updated
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, model_id, expected):
        async def operation(repo):
            query = {"modelId": model_id}
            document = await self.require(repo, "semantic_models", query, "Model")
            self.check_version(document, expected)
            await self.ensure_empty(
                repo,
                "semantic_revisions",
                query,
                "revisionId",
                "Delete draft revisions before deleting their model; published history cannot be deleted.",
            )
            if not await repo.delete("semantic_models", {**query, "version": expected}):
                raise Conflict()

        await self.store.transaction(operation)
