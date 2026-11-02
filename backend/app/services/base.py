from datetime import UTC, datetime

from ..core.errors import Conflict, ImmutableRevision, MetadataError, NotFound
from ..repositories.base import MetadataStore
from ..validation.graph import SemanticGraphValidator


def now():
    return datetime.now(UTC)


def scope(model_id, revision_id):
    return {"modelId": model_id, "revisionId": revision_id}


class Service:
    def __init__(self, store: MetadataStore, max_objects: int = 5000):
        self.store = store
        self.max_objects = max_objects

    async def require(self, repo, collection, query, resource):
        doc = await repo.get(collection, query)
        if doc is None:
            raise NotFound(resource)
        return doc

    @staticmethod
    def check_version(document, expected):
        if document["version"] != expected:
            raise Conflict()

    async def touch_revision(
        self, repo, model_id, revision_id, *, allow_published=False
    ):
        query = scope(model_id, revision_id)
        revision = await self.require(repo, "semantic_revisions", query, "Revision")
        if revision["approvalStatus"] != "draft":
            if allow_published:
                return revision
            raise ImmutableRevision()
        updated = {**revision, "version": revision["version"] + 1, "updatedAt": now()}
        if not await repo.replace(
            "semantic_revisions",
            {**query, "version": revision["version"], "approvalStatus": "draft"},
            updated,
        ):
            raise Conflict()
        return updated

    async def objects(self, repo, model_id, revision_id):
        docs = await repo.list(
            "semantic_objects",
            scope(model_id, revision_id),
            key="objectId",
            limit=self.max_objects + 1,
        )
        if len(docs) > self.max_objects:
            raise MetadataError(
                "object_limit",
                "This revision exceeds the configured metadata object limit.",
            )
        from .measure_types import normalize_measure

        return [normalize_measure(document) for document in docs]

    async def effective(self, repo, model_id, revision_id, objects):
        from ..engine.resolver import EffectiveSchemaResolver

        return await EffectiveSchemaResolver.load(
            repo, model_id, revision_id, objects, self.max_objects
        )

    async def page(self, collection, query, key, cursor, limit):
        docs = await self.store.repository.list(
            collection, query, key=key, after=cursor, limit=limit + 1
        )
        return {
            "items": docs[:limit],
            "nextCursor": docs[limit - 1][key] if len(docs) > limit else None,
        }

    @staticmethod
    def validate(objects, base_id, *, publishing=False):
        report = SemanticGraphValidator().validate(
            objects, base_id, publishing=publishing
        )
        if not report.valid:
            raise MetadataError(
                "invalid_semantics",
                "Metadata violates semantic contracts.",
                details=report.model_dump()["diagnostics"],
            )

    async def ensure_empty(self, repo, collection, query, key, message):
        if await repo.list(collection, query, key=key, limit=1):
            raise Conflict(message)
