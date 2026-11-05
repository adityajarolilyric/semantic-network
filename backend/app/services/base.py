from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ObjectLookup, RevisionLookup
from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..core.metadata import revision_scope as scope
from ..core.metadata import utc_now as now
from ..repositories.base import MetadataStore
from ..validation.graph import SemanticGraphValidator

__all__ = ["Service", "now", "scope"]


class Service:
    def __init__(self, store: MetadataStore, max_objects: int = 5000):
        self.store = store
        self.max_objects = max_objects

    @staticmethod
    def check_version(document, expected):
        if document["version"] != expected:
            raise Conflict()

    async def touch_revision(
        self, repo, model_id, revision_id, *, allow_published=False
    ):
        revision = await metadata_repositories(repo).revisions.require(
            RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
        )
        if revision["approvalStatus"] != "draft":
            if allow_published:
                return revision
            raise ImmutableRevision()
        updated = {**revision, "version": revision["version"] + 1, "updatedAt": now()}
        if not await metadata_repositories(repo).revisions.replace(
            RevisionLookup(
                model_id=model_id,
                revision_id=revision_id,
                version=revision["version"],
                approval_status="draft",
            ),
            updated,
        ):
            raise Conflict()
        return updated

    async def objects(self, repo, model_id, revision_id):
        docs = await metadata_repositories(repo).objects.list(
            ObjectLookup(model_id=model_id, revision_id=revision_id),
            order_by="objectId",
            limit=self.max_objects + 1,
        )
        if len(docs) > self.max_objects:
            raise MetadataError(
                "object_limit",
                "This revision exceeds the configured metadata object limit.",
            )
        from ..domain.measure_policies import normalize_measure

        return [normalize_measure(document) for document in docs]

    async def effective(self, repo, model_id, revision_id, objects):
        from ..engine.resolver import EffectiveSchemaResolver

        return await EffectiveSchemaResolver.load(
            repo, model_id, revision_id, objects, self.max_objects
        )

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
