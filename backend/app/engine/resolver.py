from copy import deepcopy
from ..core.errors import MetadataError
from ..services.base import scope


class EffectiveSchemaResolver:
    """Raw objects remain immutable catalog records; graph inputs get effective columns."""

    @staticmethod
    def apply(objects, managed):
        by_id = {d["sourceId"]: d for d in managed}
        result = deepcopy(objects)
        for obj in result:
            if obj["kind"] == "source" and obj["objectId"] in by_id:
                definition = by_id[obj["objectId"]]["definition"]
                obj["definition"]["columns"] = [
                    {k: v for k, v in c.items() if k != "lineage"}
                    for c in definition["columns"]
                ]
                obj["definition"]["keyColumns"] = definition["primaryKeyColumnIds"]
        return result

    @classmethod
    async def load(cls, repo, model_id, revision_id, objects, limit=5000):
        managed = await repo.list(
            "semantic_managed_tables",
            scope(model_id, revision_id),
            key="sourceId",
            limit=limit + 1,
        )
        if len(managed) > limit:
            raise MetadataError("object_limit", "Managed table limit exceeded.")
        return cls.apply(objects, managed)
