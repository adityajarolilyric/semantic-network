"""Default semantic onboarding for imported warehouse tables."""

import asyncio
from time import monotonic
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..domain.typed_values import NUMERIC, result_type
from ..engine.contracts import digest
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    BindingLookup,
    ManagedTableLookup,
    ObjectLookup,
)
from ..schemas.dimension_members import DimensionMembersInput
from ..schemas.dimensions import DimensionInput
from ..schemas.transactions import TransactionCreateInput
from .base import Service, now, scope
from .dimension_grains import dimension_grain


class UploadMetadataService(Service):
    """Use the existing managed-view lifecycle, then atomically author defaults.

    Stable source-derived identities make import retries safe without overwriting
    definitions that the user subsequently edits. No database clients live here.
    """

    def __init__(self, store, max_objects, workspace, managed, transactions):
        super().__init__(store, max_objects)
        self.workspace = workspace
        self.managed = managed
        self.transactions = transactions

    async def onboard(self, table, import_id):
        model = await self.workspace.bootstrap()
        model_id, revision_id = model["modelId"], model["draftRevisionId"]
        if not revision_id:
            raise Conflict("A draft revision is required to configure uploaded tables.")
        if not await self.is_scoped(model_id, revision_id, table):
            key = "upload_scope_" + digest([table, import_id])[:32]
            run = await self.managed.enable(model_id, revision_id, table, key)
            if run["state"] in {"failed", "cancelled", "superseded"}:
                run = await self.managed.enable(
                    model_id, revision_id, table, "upload_scope_" + uuid4().hex
                )
            deadline = monotonic() + 30
            while run["state"] in {"queued", "running"} and monotonic() < deadline:
                await asyncio.sleep(0.1)
                run = await self.managed.run_status(model_id, revision_id, run["runId"])
            if run["state"] != "succeeded":
                raise MetadataError(
                    "upload_scope_incomplete",
                    "The CSV is stored, but its managed view is not ready. Retry this upload with the same request to finish setup.",
                )

        async def operation(repo):
            objects = await self.definitions(repo, model_id, revision_id)
            source = next(
                obj
                for obj in objects
                if obj["kind"] == "source" and obj["definition"]["tableName"] == table
            )
            # A retried upload must never replace manually authored metadata.
            if any(
                (
                    obj["kind"] == "transaction"
                    and obj["definition"]["sourceId"] == source["objectId"]
                )
                or (
                    obj["kind"] == "dimension"
                    and obj.get("memberCatalog", {}).get("sourceId")
                    == source["objectId"]
                )
                for obj in objects
            ):
                return []
            documents, warnings = self.defaults(source, model_id, revision_id, objects)
            if not documents:
                return warnings
            if len(objects) + len(documents) > self.max_objects:
                raise Conflict("The revision has reached its metadata object limit.")
            revision = await self.touch_revision(repo, model_id, revision_id)
            self.validate(
                await self.effective(
                    repo, model_id, revision_id, [*objects, *documents]
                ),
                revision["baseDimensionId"],
            )
            for document in documents:
                existing = await metadata_repositories(repo).objects.find(
                    ObjectLookup(
                        model_id=model_id,
                        revision_id=revision_id,
                        object_id=document["objectId"],
                    )
                )
                if existing:
                    raise Conflict("An automatic metadata identity is already in use.")
                await metadata_repositories(repo).objects.insert(document)
            return warnings

        return await self.store.transaction(operation)

    async def is_scoped(self, model_id, revision_id, table):
        repositories = metadata_repositories(self.store.repository)
        sources = await repositories.objects.list(
            ObjectLookup(model_id=model_id, revision_id=revision_id, kind="source"),
            order_by="objectId",
            limit=self.max_objects,
        )
        source = next(
            (obj for obj in sources if obj["definition"]["tableName"] == table), None
        )
        if not source or not source["definition"]["inScope"]:
            return False
        managed = await repositories.managed_tables.find(
            ManagedTableLookup(
                model_id=model_id,
                revision_id=revision_id,
                source_id=source["objectId"],
            )
        )
        binding = await repositories.bindings.find(
            BindingLookup(
                model_id=model_id,
                revision_id=revision_id,
                source_id=source["objectId"],
            )
        )
        return bool(
            managed
            and binding
            and managed["definitionHash"] == binding["definitionHash"]
        )

    def defaults(self, source, model_id, revision_id, objects):
        columns = source["definition"]["columns"]
        numeric = [
            col
            for col in columns
            if (result_type(col["physicalType"]) or {}).get("type") in NUMERIC
        ]
        keys = [col for col in columns if col not in numeric] if numeric else columns
        if not keys or len(keys) > 32 or len(numeric) > 128:
            return [], [
                "Table scoped. Choose grain keys and create its semantic object manually: automatic setup requires 1–32 non-numeric keys and at most 128 numeric measures."
            ]
        token = digest(["uploaded-table", source["objectId"]])[:28]
        common = {"name": source["name"], "displayName": source["name"]}
        timestamp = now()
        metadata = {
            **scope(model_id, revision_id),
            "version": 1,
            "createdAt": timestamp,
            "updatedAt": timestamp,
        }
        if numeric:
            payload = TransactionCreateInput.model_validate(
                {
                    **common,
                    "objectId": "transaction_" + token,
                    "definition": {
                        "sourceId": source["objectId"],
                        "grainColumnIds": [col["columnId"] for col in keys],
                    },
                    "measures": [
                        {
                            "valueColumnId": col["columnId"],
                            "editable": False,
                            "aggregation": {"nonTime": "sum", "time": "sum"},
                        }
                        for col in numeric
                    ],
                }
            )
            transaction = {**self.transactions.create_input(payload), **metadata}
            measures = self.transactions.additional_create_documents(
                payload, transaction, [*objects, transaction]
            )
            return [transaction, *measures], []
        members = DimensionMembersInput.model_validate(
            {
                **common,
                "objectId": "members_" + token,
                "isTimeDim": False,
                "definition": {
                    "sourceId": source["objectId"],
                    "columns": [
                        {
                            "columnId": col["columnId"],
                            "sourceColumnId": col["columnId"],
                            "name": col["name"],
                            "displayName": col["name"],
                        }
                        for col in columns
                    ],
                    "keyColumnIds": [col["columnId"] for col in keys],
                    "labelColumnId": keys[0]["columnId"],
                    "attributeColumnIds": [col["columnId"] for col in columns],
                },
            }
        )
        dimension = DimensionInput.model_validate(dimension_grain(members.model_dump()))
        return [{**dimension.model_dump(), **metadata}], []
