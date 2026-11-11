from ..core.errors import Conflict, MetadataError
from ..domain.grains import transaction_grain_id
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    BindingLookup,
    ManagedTableLookup,
    ObjectLookup,
    RevisionLookup,
)
from .base import Service, now, scope


class ObjectService(Service):
    kind: str
    object_views = None

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

    async def preview_saved(self, model_id, revision_id, object_id, sql_only=False):
        await self.get(model_id, revision_id, object_id)
        if not self.object_views:
            raise Conflict("Save the object before Preview.")
        return await self.object_views.preview(
            model_id, revision_id, object_id, sql_only
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

    def create_input(self, payload):
        return payload.model_dump()

    def additional_create_documents(self, payload, document, objects):
        return []

    def related_update_documents(self, previous, updated, objects):
        return []

    async def create(self, model_id, revision_id, payload, *, allow_existing=False):
        async def operation(repo):
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model_id, revision_id=revision_id), "Revision"
            )
            objects = await self.definitions(repo, model_id, revision_id)
            if len(objects) >= self.max_objects:
                raise Conflict("The revision has reached its metadata object limit.")
            if not allow_existing and any(
                obj["objectId"] == payload.objectId for obj in objects
            ):
                raise Conflict("An object with this ID already exists in the revision.")
            timestamp = now()
            document = {
                **self.create_input(payload),
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
            related = self.additional_create_documents(
                payload,
                document,
                await self.effective(repo, model_id, revision_id, [*objects, document]),
            )
            existing = {obj["objectId"]: obj for obj in objects}
            if allow_existing and document["objectId"] in existing:
                candidates = [document, *related]

                def content(obj):
                    return {
                        k: v
                        for k, v in obj.items()
                        if k not in {"version", "createdAt", "updatedAt", "_id"}
                    }

                if all(
                    item["objectId"] in existing
                    and content(existing[item["objectId"]]) == content(item)
                    for item in candidates
                ):
                    return existing[document["objectId"]]
                raise Conflict(
                    "The Transaction or selected Measures already exist with different metadata."
                )
            revision = await self.touch_revision(repo, model_id, revision_id)
            if len(objects) + 1 + len(related) > self.max_objects:
                raise Conflict("The revision has reached its metadata object limit.")
            existing_ids = {item["objectId"] for item in [*objects, document]}
            for item in related:
                if item["objectId"] in existing_ids:
                    raise Conflict(
                        "A related definition with this identity already exists."
                    )
                existing_ids.add(item["objectId"])
            self.validate(
                await self.effective(
                    repo, model_id, revision_id, [*objects, document, *related]
                ),
                revision["baseDimensionId"],
            )
            await metadata_repositories(repo).objects.insert(document)
            for item in related:
                await metadata_repositories(repo).objects.insert(item)
            return document

        if self.object_views:
            return await self.object_views.save(
                self, model_id, revision_id, payload, None, operation
            )
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
            objects = await self.definitions(repo, model_id, revision_id)
            updated = self.prepare_document(
                updated, await self.effective(repo, model_id, revision_id, objects)
            )
            candidates = [
                updated if obj["objectId"] == object_id else obj for obj in objects
            ]
            effective_candidates = await self.effective(
                repo, model_id, revision_id, candidates
            )
            related = self.related_update_documents(
                document, updated, effective_candidates
            )
            if related:
                replacements = {item["objectId"]: item for item in related}
                effective_candidates = await self.effective(
                    repo,
                    model_id,
                    revision_id,
                    [replacements.get(obj["objectId"], obj) for obj in candidates],
                )
            self.validate(effective_candidates, revision["baseDimensionId"])
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
            for item in related:
                if not await metadata_repositories(repo).objects.replace(
                    ObjectLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        object_id=item["objectId"],
                        kind=item["kind"],
                        version=item["version"] - 1,
                    ),
                    item,
                ):
                    raise Conflict(
                        "A related measure changed. Reload metadata and retry."
                    )
            return updated

        if self.object_views:
            return await self.object_views.save(
                self, model_id, revision_id, payload, expected, operation
            )
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
            if object_id == revision["baseDimensionId"]:
                raise Conflict(
                    "Choose or clear the revision's base dimension before deleting this level."
                )
            await metadata_repositories(repo).ensure_object_unreferenced(
                model_id,
                revision_id,
                object_id,
                "This object is referenced by evidence or examples.",
            )
            objects = await self.definitions(repo, model_id, revision_id)
            self.validate(
                await self.effective(
                    repo,
                    model_id,
                    revision_id,
                    [
                        obj
                        for obj in objects
                        if obj["objectId"]
                        not in {
                            object_id,
                            document.get("memberCatalogId"),
                        }
                    ],
                ),
                revision["baseDimensionId"],
            )
            if self.kind == "transaction" and not document["definition"].get(
                "dimensionId"
            ):
                await metadata_repositories(repo).bindings.delete(
                    BindingLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        resource_kind="measureValues",
                        resource_id=transaction_grain_id(object_id),
                    )
                )
            if self.kind in {"dimension", "transaction"}:
                await metadata_repositories(repo).bindings.delete(
                    BindingLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        resource_kind="objectView",
                        resource_id=object_id,
                    )
                )
            if self.kind == "dimension":
                await metadata_repositories(repo).bindings.delete(
                    BindingLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        resource_kind="dimension",
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
