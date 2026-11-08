"""Atomically author a Transaction, reusable population, Measures and Base selection."""

from ..core.errors import Conflict, MetadataError
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ObjectLookup, RevisionLookup
from ..schemas.transactions import TransactionDocument
from .base import Service, now, scope


class TransactionPopulationService(Service):
    def __init__(self, transactions, resolution):
        super().__init__(transactions.store, transactions.max_objects)
        self.transactions, self.resolution = transactions, resolution

    async def save(self, model, revision, command):
        async def operation(repo):
            repositories = metadata_repositories(repo)
            rev = await repositories.revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            self.check_version(rev, command.expectedRevisionVersion)
            rev = await self.touch_revision(repo, model, revision)
            objects = await self.definitions(repo, model, revision)
            previous = {obj["objectId"]: obj for obj in objects}
            writes = []
            timestamp = now()

            def stage(payload, expected):
                old = previous.get(payload.objectId)
                if old:
                    if old["kind"] != payload.kind or expected is None:
                        raise Conflict(
                            "This identity already exists. Reopen it before saving."
                        )
                    self.check_version(old, expected)
                elif expected is not None:
                    raise Conflict("This definition no longer exists.")
                document = {
                    **(old or {}),
                    **payload.model_dump(exclude={"measures"}),
                    **scope(model, revision),
                    "version": old["version"] + 1 if old else 1,
                    "createdAt": old["createdAt"] if old else timestamp,
                    "updatedAt": timestamp,
                }
                writes.append(document)
                return document

            if command.population:
                stage(command.population, command.expectedPopulationVersion)
            candidates = {**previous, **{obj["objectId"]: obj for obj in writes}}
            payload = command.transaction
            if payload.objectId == payload.definition.dimensionId:
                raise MetadataError(
                    "population_identity",
                    "Transaction and population must have distinct identities.",
                )
            document = stage(payload, command.expectedVersion)
            candidates[document["objectId"]] = document
            effective = await self.effective(
                repo, model, revision, list(candidates.values())
            )
            document = self.transactions.prepare_document(document, effective)
            old = previous.get(document["objectId"])
            if old and payload.measures:
                raise MetadataError(
                    "transaction_measures",
                    "Create additional measures from the Measures screen when editing a Transaction.",
                )
            related = (
                self.transactions.related_update_documents(old, document, effective)
                if old
                else self.transactions.additional_create_documents(
                    payload, document, effective
                )
            )
            for item in related:
                if not old and item["objectId"] in candidates:
                    raise Conflict("A selected Measure already exists.")
                candidates[item["objectId"]] = item
                writes.append(item)
            if len(candidates) > self.max_objects:
                raise Conflict("The revision has reached its metadata object limit.")
            base_id = (
                document["definition"]["dimensionId"]
                if command.useAsBase
                else rev["baseDimensionId"]
            )
            self.validate(
                await self.effective(repo, model, revision, list(candidates.values())),
                base_id,
            )
            for item in writes:
                old_item = previous.get(item["objectId"])
                if old_item:
                    if not await repositories.objects.replace(
                        ObjectLookup(
                            model_id=model,
                            revision_id=revision,
                            object_id=item["objectId"],
                            kind=item["kind"],
                            version=old_item["version"],
                        ),
                        item,
                    ):
                        raise Conflict()
                else:
                    await repositories.objects.insert(item)
            if base_id != rev["baseDimensionId"]:
                if not await repositories.revisions.replace(
                    RevisionLookup(
                        model_id=model, revision_id=revision, version=rev["version"]
                    ),
                    {**rev, "baseDimensionId": base_id},
                ):
                    raise Conflict()
            return TransactionDocument.model_validate(document).model_dump()

        return await self.store.transaction(operation)

    async def preview(self, model, revision, command, *, sql_only=False):
        return await self.resolution.preview(
            model,
            revision,
            command.transaction,
            sql_only=sql_only,
            population=command.population,
        )
