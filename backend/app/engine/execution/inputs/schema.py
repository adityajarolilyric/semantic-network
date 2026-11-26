from ....core.errors import MetadataError
from ....repositories.metadata import metadata_repositories
from ....repositories.metadata.criteria import ManagedTableLookup
from ...graph.resolved_graph import EffectiveGraph
from ...graph.schema import apply_effective_schema


class EffectiveSchemaResolver:
    """Raw objects remain immutable catalog records; graph inputs get effective columns."""

    @staticmethod
    def apply(objects, managed):
        return apply_effective_schema(objects, managed)

    @classmethod
    async def load(cls, repo, model_id, revision_id, objects, limit=5000):
        if isinstance(objects, EffectiveGraph):
            return objects
        managed = await metadata_repositories(repo).managed_tables.list(
            ManagedTableLookup(model_id=model_id, revision_id=revision_id),
            order_by="sourceId",
            limit=limit + 1,
        )
        if len(managed) > limit:
            raise MetadataError("object_limit", "Managed table limit exceeded.")
        return cls.apply(objects, managed)
