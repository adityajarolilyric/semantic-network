"""Source-member authoring is a facade over one persisted Dimension aggregate."""

from ..core.errors import Conflict
from ..domain.dimensions import expand_member_catalogs
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ObjectLookup
from ..schemas.dimensions import DimensionInput
from .base import Service
from .dimension_grains import dimension_grain


class DimensionMembersService(Service):
    def __init__(self, store, max_objects, dimensions):
        super().__init__(store, max_objects)
        self.dimensions = dimensions

    @staticmethod
    def members(dimension):
        return next(
            obj
            for obj in expand_member_catalogs([dimension])
            if obj["kind"] == "dimensionMembers"
        )

    async def owner(self, model, revision, catalog_id):
        return await metadata_repositories(self.store.repository).objects.require(
            ObjectLookup(
                model_id=model,
                revision_id=revision,
                kind="dimension",
                member_catalog_id=catalog_id,
            ),
            "Dimension",
        )

    async def get(self, model, revision, catalog_id):
        return self.members(await self.owner(model, revision, catalog_id))

    async def list(self, model, revision, cursor, limit):
        # Ordering/pagination is by the embedded stable catalog identity.
        dimensions = await self.dimensions.list(model, revision, None, self.max_objects)
        items = sorted(
            [self.members(d) for d in dimensions["items"] if d.get("memberCatalog")],
            key=lambda d: d["objectId"],
        )
        if cursor:
            items = [item for item in items if item["objectId"] > cursor]
        return {
            "items": items[:limit],
            "nextCursor": items[limit - 1]["objectId"] if len(items) > limit else None,
        }

    async def create(self, model, revision, payload):
        dimension = await self.dimensions.create(
            model,
            revision,
            DimensionInput.model_validate(dimension_grain(payload.model_dump())),
        )
        return self.members(dimension)

    async def update(self, model, revision, catalog_id, payload, expected):
        owner = await self.owner(model, revision, catalog_id)
        if payload.objectId != catalog_id:
            raise Conflict("Dimension identity cannot change.")
        updated = dimension_grain(payload.model_dump())
        updated["objectId"] = owner["objectId"]
        return self.members(
            await self.dimensions.update(
                model,
                revision,
                owner["objectId"],
                DimensionInput.model_validate(updated),
                expected,
            )
        )

    async def ensure_grain(self, model, revision, catalog_id):
        return await self.owner(model, revision, catalog_id)

    async def delete(self, model, revision, catalog_id, expected):
        owner = await self.owner(model, revision, catalog_id)
        await self.dimensions.delete(model, revision, owner["objectId"], expected)
