"""Repository mechanics; collection names/selectors never escape this layer."""

from ...core.errors import Conflict, NotFound


class CollectionRepository:
    collection: str
    default_order: str
    criteria_type: type

    def __init__(self, documents):
        self.documents = documents

    def selector(self, lookup):
        if not isinstance(lookup, self.criteria_type):
            raise TypeError(
                f"{type(self).__name__} requires {self.criteria_type.__name__}"
            )
        return lookup.mongo_selector()

    async def find(self, lookup):
        return await self.documents.get(self.collection, self.selector(lookup))

    async def require(self, lookup, resource):
        document = await self.find(lookup)
        if document is None:
            raise NotFound(resource)
        return document

    async def list(
        self, lookup, *, limit=100, after=None, newest_first=False, order_by=None
    ):
        return await self.documents.list(
            self.collection,
            self.selector(lookup),
            key=order_by or self.default_order,
            after=after,
            limit=limit,
            descending=newest_first,
        )

    async def page(self, lookup, cursor, limit):
        documents = await self.list(lookup, limit=limit + 1, after=cursor)
        return {
            "items": documents[:limit],
            "nextCursor": documents[limit - 1][self.default_order]
            if len(documents) > limit
            else None,
        }

    async def insert(self, document):
        return await self.documents.insert(self.collection, document)

    async def replace(self, lookup, document):
        return await self.documents.replace(
            self.collection, self.selector(lookup), document
        )

    async def delete(self, lookup):
        return await self.documents.delete(self.collection, self.selector(lookup))

    async def ensure_empty(self, lookup, message):
        if await self.list(lookup, limit=1):
            raise Conflict(message)
