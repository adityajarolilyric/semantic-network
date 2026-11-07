from ..core.errors import ImmutableRevision
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ObjectLookup, RevisionLookup
from ..schemas.dimension_members import DimensionMembersInput
from ..schemas.dimensions import DimensionInput
from .dimension_grains import dimension_grain
from .objects import ObjectService


class DimensionService(ObjectService):
    kind = "dimension"

    async def create(self, model_id, revision_id, payload):
        if isinstance(payload, DimensionMembersInput):
            payload = DimensionInput.model_validate(
                dimension_grain(payload.model_dump())
            )
        return await super().create(model_id, revision_id, payload)

    async def update(self, model_id, revision_id, object_id, payload, expected):
        async def unchanged(repo):
            repositories = metadata_repositories(repo)
            revision = await repositories.revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            if revision["approvalStatus"] != "draft":
                raise ImmutableRevision()
            document = await repositories.objects.require(
                ObjectLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    object_id=object_id,
                    kind=self.kind,
                ),
                "Dimension",
            )
            self.check_version(document, expected)
            return (
                document
                if all(
                    document.get(key) == value
                    for key, value in payload.model_dump().items()
                )
                else None
            )

        current = await self.store.transaction(unchanged)
        if current is not None:
            return current
        return await super().update(model_id, revision_id, object_id, payload, expected)
