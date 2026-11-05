from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    BindingLookup,
    ManagedTableLookup,
    ObjectLookup,
    RevisionLookup,
)
from ..core.errors import Conflict, MetadataError
from .base import Service, now, scope


class ObjectService(Service):
    kind: str

    async def get(self, model_id, revision_id, object_id):
        return await metadata_repositories(self.store.repository).objects.require(
            ObjectLookup(
                model_id=model_id,
                revision_id=revision_id,
                object_id=object_id,
                kind=self.kind,
            ),
            self.kind,
        )

    async def list(self, model_id, revision_id, cursor, limit):
        await metadata_repositories(self.store.repository).revisions.require(
            RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
        )
        return await metadata_repositories(self.store.repository).objects.page(
            ObjectLookup(model_id=model_id, revision_id=revision_id, kind=self.kind),
            cursor,
            limit,
        )

    def prepare_document(self, document, objects):
        return document

    async def create(self, model_id, revision_id, payload):
        async def operation(repo):
            revision = await self.touch_revision(repo, model_id, revision_id)
            objects = await self.objects(repo, model_id, revision_id)
            if len(objects) >= self.max_objects:
                raise Conflict("The revision has reached its metadata object limit.")
            if any(obj["objectId"] == payload.objectId for obj in objects):
                raise Conflict("An object with this ID already exists in the revision.")
            timestamp = now()
            document = {
                **payload.model_dump(),
                **scope(model_id, revision_id),
                "version": 1,
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
            document = self.prepare_document(
                document, await self.effective(repo, model_id, revision_id, objects)
            )
            if (
                self.kind == "source"
                and revision["metadataContractVersion"] >= 2
                and document["definition"]["inScope"]
            ):
                raise Conflict("Enable sources through the managed-table lifecycle.")
            self.validate(
                await self.effective(repo, model_id, revision_id, [*objects, document]),
                revision["basePlanningLevelId"],
            )
            return await metadata_repositories(repo).objects.insert(document)

        return await self.store.transaction(operation)

    async def update(self, model_id, revision_id, object_id, payload, expected):
        if payload.objectId != object_id:
            raise MetadataError(
                "identity_mismatch", "objectId cannot change during an update."
            )

        async def operation(repo):
            revision = await self.touch_revision(repo, model_id, revision_id)
            document = await metadata_repositories(repo).objects.require(
                ObjectLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    object_id=object_id,
                    kind=self.kind,
                ),
                self.kind,
            )
            self.check_version(document, expected)
            if self.kind == "source":
                old, new = document["definition"], payload.definition.model_dump()
                if document["name"] != payload.name or any(
                    old[key] != new[key] for key in ("tableName", "columns")
                ):
                    raise Conflict(
                        "Source names and discovered column definitions are read-only; dimension labels are editable."
                    )
            if (
                self.kind == "source"
                and revision["metadataContractVersion"] >= 2
                and document["definition"]["inScope"] != payload.definition.inScope
            ):
                raise Conflict(
                    "Change managed availability through the managed-table lifecycle."
                )
            updated = {
                **document,
                **payload.model_dump(),
                "version": expected + 1,
                "updatedAt": now(),
            }
            objects = await self.objects(repo, model_id, revision_id)
            updated = self.prepare_document(
                updated, await self.effective(repo, model_id, revision_id, objects)
            )
            self.validate(
                await self.effective(
                    repo,
                    model_id,
                    revision_id,
                    [
                        updated if obj["objectId"] == object_id else obj
                        for obj in objects
                    ],
                ),
                revision["basePlanningLevelId"],
            )
            if not await metadata_repositories(repo).objects.replace(
                ObjectLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    object_id=object_id,
                    kind=self.kind,
                    version=expected,
                ),
                updated,
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, model_id, revision_id, object_id, expected):
        async def operation(repo):
            revision = await self.touch_revision(repo, model_id, revision_id)
            document = await metadata_repositories(repo).objects.require(
                ObjectLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    object_id=object_id,
                    kind=self.kind,
                ),
                self.kind,
            )
            self.check_version(document, expected)
            if self.kind == "source" and await metadata_repositories(
                repo
            ).managed_tables.find(
                ManagedTableLookup(
                    model_id=model_id, revision_id=revision_id, source_id=object_id
                )
            ):
                raise Conflict(
                    "A managed source cannot be deleted; disable it to retain its revision history."
                )
            if object_id == revision["basePlanningLevelId"]:
                raise Conflict(
                    "Choose or clear the revision's base planning level before deleting this level."
                )
            await metadata_repositories(repo).ensure_object_unreferenced(
                model_id,
                revision_id,
                object_id,
                "This object is referenced by evidence or examples.",
            )
            objects = await self.objects(repo, model_id, revision_id)
            self.validate(
                await self.effective(
                    repo,
                    model_id,
                    revision_id,
                    [obj for obj in objects if obj["objectId"] != object_id],
                ),
                revision["basePlanningLevelId"],
            )
            if self.kind == "planningLevel":
                await metadata_repositories(repo).bindings.delete(
                    BindingLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        resource_kind="planningLevel",
                        resource_id=object_id,
                    )
                )
                await metadata_repositories(repo).bindings.delete(
                    BindingLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        resource_kind="measureValues",
                        resource_id=object_id,
                    )
                )
            if not await metadata_repositories(repo).objects.delete(
                ObjectLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    object_id=object_id,
                    kind=self.kind,
                    version=expected,
                )
            ):
                raise Conflict()

        await self.store.transaction(operation)
