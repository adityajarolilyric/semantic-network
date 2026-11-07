from ..core.errors import Conflict, MetadataError
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ObjectLookup, RecordLookup, RevisionLookup
from ..repositories.metadata.records import RecordCategory
from .base import Service, now, scope


class RevisionRecordService(Service):
    """Evidence can evolve after publication; examples belong to the frozen definition."""

    record_category: RecordCategory
    allow_published = False

    async def get(self, model_id, revision_id, record_id):
        return (
            await metadata_repositories(self.store.repository)
            .records(self.record_category)
            .require(
                RecordLookup(
                    model_id=model_id, revision_id=revision_id, record_id=record_id
                ),
                "Record",
            )
        )

    async def list(self, model_id, revision_id, cursor, limit):
        await metadata_repositories(self.store.repository).revisions.require(
            RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
        )
        return (
            await metadata_repositories(self.store.repository)
            .records(self.record_category)
            .page(
                RecordLookup(model_id=model_id, revision_id=revision_id), cursor, limit
            )
        )

    async def validate_record(self, repo, model_id, revision_id, payload):
        for object_id in payload.objectIds:
            await metadata_repositories(repo).objects.require(
                ObjectLookup(
                    model_id=model_id, revision_id=revision_id, object_id=object_id
                ),
                "Referenced object",
            )
        for fixture in getattr(payload, "fixtures", []):
            source = await metadata_repositories(repo).objects.require(
                ObjectLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    object_id=fixture.sourceId,
                    kind="source",
                ),
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
            existing = (
                await metadata_repositories(repo)
                .records(self.record_category)
                .list(
                    RecordLookup(model_id=model_id, revision_id=revision_id),
                    order_by="recordId",
                    limit=self.max_objects,
                )
            )
            if len(existing) >= self.max_objects:
                raise Conflict("The revision has reached its record limit.")
            await self.validate_record(repo, model_id, revision_id, payload)
            timestamp = now()
            return (
                await metadata_repositories(repo)
                .records(self.record_category)
                .insert(
                    {
                        **payload.model_dump(),
                        **scope(model_id, revision_id),
                        "version": 1,
                        "createdAt": timestamp,
                        "updatedAt": timestamp,
                    }
                )
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
            document = (
                await metadata_repositories(repo)
                .records(self.record_category)
                .require(
                    RecordLookup(
                        model_id=model_id, revision_id=revision_id, record_id=record_id
                    ),
                    "Record",
                )
            )
            self.check_version(document, expected)
            await self.validate_record(repo, model_id, revision_id, payload)
            updated = {
                **document,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            if (
                not await metadata_repositories(repo)
                .records(self.record_category)
                .replace(
                    RecordLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        record_id=record_id,
                        version=expected,
                    ),
                    updated,
                )
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, model_id, revision_id, record_id, expected):
        async def operation(repo):
            await self.touch_revision(
                repo, model_id, revision_id, allow_published=self.allow_published
            )
            document = (
                await metadata_repositories(repo)
                .records(self.record_category)
                .require(
                    RecordLookup(
                        model_id=model_id, revision_id=revision_id, record_id=record_id
                    ),
                    "Record",
                )
            )
            self.check_version(document, expected)
            if (
                not await metadata_repositories(repo)
                .records(self.record_category)
                .delete(
                    RecordLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        record_id=record_id,
                        version=expected,
                    )
                )
            ):
                raise Conflict()

        await self.store.transaction(operation)
