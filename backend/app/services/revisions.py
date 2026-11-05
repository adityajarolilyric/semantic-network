from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.records import RecordCategory
from ..repositories.metadata.criteria import (
    ManagedTableLookup,
    ModelLookup,
    RecordLookup,
    RevisionLookup,
)
from ..core.errors import Conflict, ImmutableRevision
from ..validation.graph import SemanticGraphValidator
from .base import Service, now


class RevisionService(Service):
    async def create(self, model_id, payload):
        async def operation(repo):
            model = await metadata_repositories(repo).models.require(
                ModelLookup(model_id=model_id), "Model"
            )
            self.check_version(model, payload.expectedModelVersion)
            if model["draftRevisionId"] is not None:
                raise Conflict("This model already has a draft revision.")
            parent_objects = []
            if payload.parentRevisionId:
                parent = await metadata_repositories(repo).revisions.require(
                    RevisionLookup(
                        model_id=model_id, revision_id=payload.parentRevisionId
                    ),
                    "Parent revision",
                )
                if parent["approvalStatus"] != "published":
                    raise Conflict("A parent revision must be published.")
                parent_objects = await self.objects(
                    repo, model_id, payload.parentRevisionId
                )
                parent_base = parent["basePlanningLevelId"]
            else:
                parent_base = None
            timestamp = now()
            document = {
                "modelId": model_id,
                "revisionId": payload.revisionId,
                "revisionNumber": model["revisionCounter"] + 1,
                "parentRevisionId": payload.parentRevisionId,
                "basePlanningLevelId": payload.basePlanningLevelId or parent_base,
                "metadataContractVersion": max(
                    payload.metadataContractVersion,
                    parent["metadataContractVersion"]
                    if payload.parentRevisionId
                    else 1,
                ),
                "changeReason": payload.changeReason,
                "approvalStatus": "draft",
                "version": 1,
                "createdAt": timestamp,
                "updatedAt": timestamp,
                "publishedAt": None,
            }
            await metadata_repositories(repo).revisions.insert(document)
            for obj in parent_objects:
                await metadata_repositories(repo).objects.insert(
                    {
                        **obj,
                        "revisionId": payload.revisionId,
                        "version": 1,
                        "createdAt": timestamp,
                        "updatedAt": timestamp,
                    }
                )
            if payload.parentRevisionId:
                managed = await metadata_repositories(repo).managed_tables.list(
                    ManagedTableLookup(
                        model_id=model_id, revision_id=payload.parentRevisionId
                    ),
                    order_by="sourceId",
                    limit=self.max_objects,
                )
                for item in managed:
                    await metadata_repositories(repo).managed_tables.insert(
                        {
                            **item,
                            "revisionId": payload.revisionId,
                            "version": 1,
                            "createdAt": timestamp,
                            "updatedAt": timestamp,
                        }
                    )
                examples = (
                    await metadata_repositories(repo)
                    .records(RecordCategory.EXAMPLE)
                    .list(
                        RecordLookup(
                            model_id=model_id, revision_id=payload.parentRevisionId
                        ),
                        order_by="recordId",
                        limit=self.max_objects + 1,
                    )
                )
                if len(examples) > self.max_objects:
                    raise Conflict(
                        "Parent revision exceeds the configured example limit."
                    )
                for example in examples:
                    await (
                        metadata_repositories(repo)
                        .records(RecordCategory.EXAMPLE)
                        .insert(
                            {
                                **example,
                                "revisionId": payload.revisionId,
                                "version": 1,
                                "createdAt": timestamp,
                                "updatedAt": timestamp,
                            }
                        )
                    )
            updated = {
                **model,
                "draftRevisionId": payload.revisionId,
                "revisionCounter": document["revisionNumber"],
                "version": model["version"] + 1,
                "updatedAt": timestamp,
            }
            if not await metadata_repositories(repo).models.replace(
                ModelLookup(model_id=model_id, version=model["version"]), updated
            ):
                raise Conflict()
            return document

        return await self.store.transaction(operation)

    async def get(self, model_id, revision_id):
        return await metadata_repositories(self.store.repository).revisions.require(
            RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
        )

    async def list(self, model_id, cursor, limit):
        await metadata_repositories(self.store.repository).models.require(
            ModelLookup(model_id=model_id), "Model"
        )
        return await metadata_repositories(self.store.repository).revisions.page(
            RevisionLookup(model_id=model_id), cursor, limit
        )

    async def update(self, model_id, revision_id, payload, expected):
        async def operation(repo):
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            self.check_version(revision, expected)
            if revision["approvalStatus"] != "draft":
                raise ImmutableRevision()
            objects = await self.objects(repo, model_id, revision_id)
            if payload.basePlanningLevelId:
                candidate = next(
                    (
                        o
                        for o in objects
                        if o["objectId"] == payload.basePlanningLevelId
                        and o["kind"] == "planningLevel"
                    ),
                    None,
                )
                if candidate is None:
                    raise Conflict(
                        "The selected base planning level must exist in this revision."
                    )
            self.validate(
                await self.effective(repo, model_id, revision_id, objects),
                payload.basePlanningLevelId,
            )
            updated = {
                **revision,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            if not await metadata_repositories(repo).revisions.replace(
                RevisionLookup(
                    model_id=model_id, revision_id=revision_id, version=expected
                ),
                updated,
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def validate_revision(self, model_id, revision_id):
        async def operation(repo):
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            objects = await self.objects(repo, model_id, revision_id)
            return SemanticGraphValidator().validate(
                await self.effective(repo, model_id, revision_id, objects),
                revision["basePlanningLevelId"],
                publishing=True,
            )

        return await self.store.transaction(operation)

    async def publish(self, model_id, revision_id, payload, expected):
        if hasattr(self, "managed_tables"):
            checked = await self.managed_tables.preflight(model_id, revision_id)
            if checked != expected:
                raise Conflict()
            revision = await self.get(model_id, revision_id)
            if revision["metadataContractVersion"] >= 2:
                await self.planning_builds.preflight(model_id, revision_id)

        async def operation(repo):
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            self.check_version(revision, expected)
            if revision["approvalStatus"] != "draft":
                raise ImmutableRevision()
            model = await metadata_repositories(repo).models.require(
                ModelLookup(model_id=model_id), "Model"
            )
            self.check_version(model, payload.expectedModelVersion)
            if model["draftRevisionId"] != revision_id:
                raise Conflict("Only the model's current draft may be published.")
            self.validate(
                await self.effective(
                    repo,
                    model_id,
                    revision_id,
                    await self.objects(repo, model_id, revision_id),
                ),
                revision["basePlanningLevelId"],
                publishing=True,
            )
            timestamp = now()
            updated_revision = {
                **revision,
                "approvalStatus": "published",
                "version": expected + 1,
                "updatedAt": timestamp,
                "publishedAt": timestamp,
            }
            if not await metadata_repositories(repo).revisions.replace(
                RevisionLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    version=expected,
                    approval_status="draft",
                ),
                updated_revision,
            ):
                raise Conflict()
            updated_model = {
                **model,
                "publishedRevisionId": revision_id,
                "draftRevisionId": None,
                "version": model["version"] + 1,
                "updatedAt": timestamp,
            }
            if not await metadata_repositories(repo).models.replace(
                ModelLookup(model_id=model_id, version=model["version"]), updated_model
            ):
                raise Conflict()
            return updated_revision

        return await self.store.transaction(operation)

    async def delete(self, model_id, revision_id, expected):
        async def operation(repo):
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            self.check_version(revision, expected)
            if revision["approvalStatus"] != "draft":
                raise ImmutableRevision()
            await metadata_repositories(repo).ensure_revision_empty(
                model_id,
                revision_id,
                "Delete revision contents explicitly before deleting the draft.",
            )
            model = await metadata_repositories(repo).models.require(
                ModelLookup(model_id=model_id), "Model"
            )
            if not await metadata_repositories(repo).revisions.delete(
                RevisionLookup(
                    model_id=model_id, revision_id=revision_id, version=expected
                )
            ):
                raise Conflict()
            if model["draftRevisionId"] == revision_id:
                updated = {
                    **model,
                    "draftRevisionId": None,
                    "version": model["version"] + 1,
                    "updatedAt": now(),
                }
                if not await metadata_repositories(repo).models.replace(
                    ModelLookup(model_id=model_id, version=model["version"]), updated
                ):
                    raise Conflict()

        await self.store.transaction(operation)
