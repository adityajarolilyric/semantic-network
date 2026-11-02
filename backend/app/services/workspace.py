from .base import Service, now


class WorkspaceService(Service):
    """Atomically initialize a single default workspace, safe under competing clients."""

    async def bootstrap(self):
        return await self.store.transaction(self.initialize)

    async def initialize(self, repo):
        models = await repo.list("semantic_models", {}, key="modelId", limit=1)
        if models:
            return models[0]
        timestamp = now()
        model = {
            "modelId": "default_model",
            "name": "PlanningModel",
            "displayName": "Planning Model",
            "description": "",
            "draftRevisionId": "revision_1",
            "publishedRevisionId": None,
            "revisionCounter": 1,
            "version": 1,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        await repo.insert("semantic_models", model)
        await repo.insert(
            "semantic_revisions",
            {
                "modelId": "default_model",
                "revisionId": "revision_1",
                "revisionNumber": 1,
                "parentRevisionId": None,
                "basePlanningLevelId": None,
                "changeReason": "Initial semantic definition",
                "metadataContractVersion": 2,
                "approvalStatus": "draft",
                "version": 1,
                "createdAt": timestamp,
                "updatedAt": timestamp,
                "publishedAt": None,
            },
        )
        return model
