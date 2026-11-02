from ..core.errors import Conflict, MetadataError
from .base import Service, now, scope


class ObjectService(Service):
    kind: str

    async def get(self, model_id, revision_id, object_id):
        query = {
            **scope(model_id, revision_id),
            "objectId": object_id,
            "kind": self.kind,
        }
        return await self.require(
            self.store.repository, "semantic_objects", query, self.kind
        )

    async def list(self, model_id, revision_id, cursor, limit):
        await self.require(
            self.store.repository,
            "semantic_revisions",
            scope(model_id, revision_id),
            "Revision",
        )
        return await self.page(
            "semantic_objects",
            {**scope(model_id, revision_id), "kind": self.kind},
            "objectId",
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
            return await repo.insert("semantic_objects", document)

        return await self.store.transaction(operation)

    async def update(self, model_id, revision_id, object_id, payload, expected):
        if payload.objectId != object_id:
            raise MetadataError(
                "identity_mismatch", "objectId cannot change during an update."
            )

        async def operation(repo):
            revision = await self.touch_revision(repo, model_id, revision_id)
            query = {
                **scope(model_id, revision_id),
                "objectId": object_id,
                "kind": self.kind,
            }
            document = await self.require(repo, "semantic_objects", query, self.kind)
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
            if not await repo.replace(
                "semantic_objects", {**query, "version": expected}, updated
            ):
                raise Conflict()
            return updated

        return await self.store.transaction(operation)

    async def delete(self, model_id, revision_id, object_id, expected):
        async def operation(repo):
            revision = await self.touch_revision(repo, model_id, revision_id)
            query = {
                **scope(model_id, revision_id),
                "objectId": object_id,
                "kind": self.kind,
            }
            document = await self.require(repo, "semantic_objects", query, self.kind)
            self.check_version(document, expected)
            if self.kind == "source" and await repo.get(
                "semantic_managed_tables",
                {**scope(model_id, revision_id), "sourceId": object_id},
            ):
                raise Conflict(
                    "A managed source cannot be deleted; disable it to retain its revision history."
                )
            if object_id == revision["basePlanningLevelId"]:
                raise Conflict(
                    "Choose or clear the revision's base planning level before deleting this level."
                )
            for collection in ("semantic_evidence", "semantic_examples"):
                records = await repo.list(
                    collection,
                    {**scope(model_id, revision_id), "objectIds": object_id},
                    key="recordId",
                    limit=1,
                )
                if records:
                    raise Conflict("This object is referenced by evidence or examples.")
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
                await repo.delete(
                    "engine_artifact_bindings",
                    {
                        **scope(model_id, revision_id),
                        "resourceKind": "planningLevel",
                        "resourceId": object_id,
                    },
                )
                await repo.delete(
                    "engine_artifact_bindings",
                    {
                        **scope(model_id, revision_id),
                        "resourceKind": "measureValues",
                        "resourceId": object_id,
                    },
                )
            if not await repo.delete(
                "semantic_objects", {**query, "version": expected}
            ):
                raise Conflict()

        await self.store.transaction(operation)
