from ..core.errors import Conflict, MetadataError
from .base import Service, now, scope


class RevisionRecordService(Service):
    """Evidence can evolve after publication; examples belong to the frozen definition."""

    collection: str
    allow_published = False

    async def get(self, model_id, revision_id, record_id):
        return await self.require(
            self.store.repository,
            self.collection,
            {**scope(model_id, revision_id), "recordId": record_id},
            "Record",
        )

    async def list(self, model_id, revision_id, cursor, limit):
        await self.require(
            self.store.repository,
            "semantic_revisions",
            scope(model_id, revision_id),
            "Revision",
        )
        return await self.page(
            self.collection, scope(model_id, revision_id), "recordId", cursor, limit
        )

    async def validate_record(self, repo, model_id, revision_id, payload):
        for object_id in payload.objectIds:
            await self.require(
                repo,
                "semantic_objects",
                {**scope(model_id, revision_id), "objectId": object_id},
                "Referenced object",
            )
        for fixture in getattr(payload, "fixtures", []):
            source = await self.require(
                repo,
                "semantic_objects",
                {
                    **scope(model_id, revision_id),
                    "objectId": fixture.sourceId,
                    "kind": "source",
                },
                "Fixture source",
            )
            names = {col["name"] for col in source["definition"]["columns"]}
            if any(not set(row) <= names for row in fixture.rows):
                raise MetadataError(
                    "unknown_fixture_column",
                    "Fixture rows must use physical column names from their source.",
                )

    async def create(self, model_id, revision_id, payload):
        async def operation(repo):
            await self.touch_revision(
                repo, model_id, revision_id, allow_published=self.allow_published
            )
            existing = await repo.list(
                self.collection,
                scope(model_id, revision_id),
                key="recordId",
                limit=self.max_objects,
            )
            if len(existing) >= self.max_objects:
                raise Conflict("The revision has reached its record limit.")
            await self.validate_record(repo, model_id, revision_id, payload)
            timestamp = now()
            return await repo.insert(
                self.collection,
                {
                    **payload.model_dump(),
                    **scope(model_id, revision_id),
                    "version": 1,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                },
            )

        return await self.store.transaction(operation)

    async def update(self, model_id, revision_id, record_id, payload, expected):
        if payload.recordId != record_id:
            raise MetadataError(
                "identity_mismatch", "recordId cannot change during an update."
            )

        async def operation(repo):
            await self.touch_revision(
                repo, model_id, revision_id, allow_published=self.allow_published
            )
            query = {**scope(model_id, revision_id), "recordId": record_id}
            document = await self.require(repo, self.collection, query, "Record")
            self.check_version(document, expected)
            await self.validate_record(repo, model_id, revision_id, payload)
            updated = {
                **document,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            if not await repo.replace(
                self.collection, {**query, "version": expected}, updated
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, model_id, revision_id, record_id, expected):
        async def operation(repo):
            await self.touch_revision(
                repo, model_id, revision_id, allow_published=self.allow_published
            )
            query = {**scope(model_id, revision_id), "recordId": record_id}
            document = await self.require(repo, self.collection, query, "Record")
            self.check_version(document, expected)
            if not await repo.delete(self.collection, {**query, "version": expected}):
                raise Conflict()

        await self.store.transaction(operation)
