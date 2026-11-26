from collections.abc import Awaitable, Callable
from typing import TypeVar

from pymongo import AsyncMongoClient, IndexModel
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from ..core.config import Settings
from ..core.errors import Conflict
from ..db.schema import CollectionSchemas
from .base import DocumentRepository, MetadataStore

T = TypeVar("T")


class MongoRepository(DocumentRepository):
    def __init__(self, database, session=None):
        self.database = database
        self.session = session

    @staticmethod
    def clean(document):
        if document is None:
            return None
        return {key: value for key, value in document.items() if key != "_id"}

    async def get(self, collection, query):
        return self.clean(
            await self.database[collection].find_one(query, session=self.session)
        )

    async def list(
        self, collection, query, *, key, after=None, limit=100, descending=False
    ):
        query = dict(query)
        if after is not None:
            query[key] = {"$gt": after}
        cursor = (
            self.database[collection]
            .find(query, session=self.session)
            .sort(key, -1 if descending else 1)
            .limit(limit)
        )
        return [self.clean(doc) async for doc in cursor]

    async def insert(self, collection, document):
        try:
            await self.database[collection].insert_one(
                dict(document), session=self.session
            )
        except DuplicateKeyError as exc:
            raise Conflict("A resource with this identity already exists.") from exc
        return document

    async def replace(self, collection, query, document):
        try:
            result = await self.database[collection].replace_one(
                query, dict(document), session=self.session
            )
        except DuplicateKeyError as exc:
            raise Conflict("A resource with this identity already exists.") from exc
        return result.matched_count == 1

    async def delete(self, collection, query):
        result = await self.database[collection].delete_one(query, session=self.session)
        return result.deleted_count == 1

    async def delete_all(self, collection):
        result = await self.database[collection].delete_many({}, session=self.session)
        return result.deleted_count


class MongoMetadataStore(MetadataStore):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        self.database = None
        self._read_repository = None

    async def open(self):
        self.client = AsyncMongoClient(
            self.settings.mongo_uri.get_secret_value(),
            appname=self.settings.app_name,
            serverSelectionTimeoutMS=self.settings.mongo_timeout_ms,
            connectTimeoutMS=self.settings.mongo_timeout_ms,
            socketTimeoutMS=30000,
            waitQueueTimeoutMS=self.settings.mongo_timeout_ms,
            maxPoolSize=self.settings.mongo_pool_size,
            tz_aware=True,
        )

        hello = await self.client.admin.command("hello")
        if not hello.get("setName") and hello.get("msg") != "isdbgrid":
            raise RuntimeError(
                "Metadata writes require a MongoDB replica set or sharded deployment for transactions."
            )

        self.database = self.client[self.settings.mongo_database]
        existing = set(await self.database.list_collection_names())

        for collection in CollectionSchemas.REQUIRED:
            validator = CollectionSchemas.validator(collection)
            if collection not in existing:
                try:
                    await self.database.create_collection(
                        collection,
                        validator=validator,
                        validationLevel="strict",
                        validationAction="error",
                    )
                except Exception as exc:
                    # Another API worker may have created the collection concurrently.
                    if getattr(exc, "code", None) != 48:
                        raise
            else:
                await self.database.command(
                    "collMod",
                    collection,
                    validator=validator,
                    validationLevel="strict",
                    validationAction="error",
                )
        await self.create_indexes()

    @property
    def repository(self):
        if self.database is None:
            raise RuntimeError("MongoDB store is not open")
        if self._read_repository is None:
            self._read_repository = MongoRepository(self.database)
        return self._read_repository

    async def create_indexes(self):
        definitions = {
            "workspace_control": [IndexModel([("controlId", 1)], unique=True)],
            "workspace_leases": [
                IndexModel([("leaseId", 1)], unique=True),
                IndexModel([("expiresAt", 1)]),
            ],
            "semantic_models": [IndexModel([("modelId", 1)], unique=True)],
            "semantic_revisions": [
                IndexModel([("modelId", 1), ("revisionId", 1)], unique=True),
                IndexModel([("modelId", 1), ("revisionNumber", 1)], unique=True),
            ],
            "semantic_objects": [
                IndexModel(
                    [("modelId", 1), ("revisionId", 1), ("objectId", 1)], unique=True
                ),
                IndexModel(
                    [("modelId", 1), ("revisionId", 1), ("kind", 1), ("objectId", 1)]
                ),
            ],
            "semantic_evidence": [
                IndexModel(
                    [("modelId", 1), ("revisionId", 1), ("recordId", 1)], unique=True
                )
            ],
            "semantic_examples": [
                IndexModel(
                    [("modelId", 1), ("revisionId", 1), ("recordId", 1)], unique=True
                )
            ],
            "semantic_contracts": [IndexModel([("contractId", 1)], unique=True)],
        }
        for collection, keys in {
            "semantic_managed_tables": ["modelId", "revisionId", "sourceId"],
            "engine_runs": ["runId"],
            "engine_run_inputs": ["runId", "inputId"],
            "engine_run_steps": ["runId", "stepId"],
            "engine_artifacts": ["artifactId"],
            "engine_table_resources": [
                "modelId",
                "revisionId",
                "resourceKind",
                "resourceId",
            ],
            "engine_read_leases": ["readId", "artifactId"],
            "engine_artifact_pins": ["runId", "artifactId"],
        }.items():
            definitions[collection] = [IndexModel([(k, 1) for k in keys], unique=True)]
        definitions["engine_artifacts"].append(
            IndexModel(
                [
                    ("cleanupPending", 1),
                    ("modelId", 1),
                    ("revisionId", 1),
                    ("artifactId", 1),
                ]
            )
        )
        # Online compatibility migration: retain legacy source lookup fields while
        # making target identity generic. Partial indexes permit planning bindings.
        bindings = self.database["engine_artifact_bindings"]
        await bindings.update_many(
            {"resourceId": {"$exists": False}, "sourceId": {"$exists": True}},
            [{"$set": {"resourceId": "$sourceId", "resourceKind": "managedTable"}}],
        )
        indexes = await bindings.index_information()
        legacy_name = "modelId_1_revisionId_1_sourceId_1"
        if legacy_name in indexes:
            try:
                await bindings.drop_index(legacy_name)
            except Exception as exc:
                if getattr(exc, "code", None) != 27:
                    raise
        definitions["engine_artifact_bindings"] = [
            IndexModel(
                [
                    ("modelId", 1),
                    ("revisionId", 1),
                    ("resourceKind", 1),
                    ("resourceId", 1),
                ],
                unique=True,
                partialFilterExpression={"resourceId": {"$exists": True}},
            ),
            IndexModel(
                [("modelId", 1), ("revisionId", 1), ("sourceId", 1)],
                unique=True,
                name="managed_source_binding",
                partialFilterExpression={"sourceId": {"$exists": True}},
            ),
        ]
        definitions["engine_table_resources"] += [
            IndexModel([("physicalName", 1)], unique=True)
        ]
        definitions["engine_read_leases"] += [
            IndexModel([("artifactId", 1), ("expiresAt", 1)]),
            IndexModel([("expiresAt", 1)], expireAfterSeconds=0),
        ]
        definitions["engine_artifact_pins"] += [IndexModel([("artifactId", 1)])]
        definitions["engine_artifacts"] += [
            IndexModel([("state", 1), ("createdAt", 1), ("artifactId", 1)])
        ]
        definitions["engine_artifact_bindings"] += [IndexModel([("artifactId", 1)])]
        definitions["semantic_objects"] += [
            IndexModel(
                [("modelId", 1), ("revisionId", 1), ("memberCatalogId", 1)],
                unique=True,
                partialFilterExpression={
                    "kind": "dimension",
                    "memberCatalogId": {"$type": "string"},
                },
            ),
        ]
        definitions["engine_runs"] += [
            IndexModel(
                [("modelId", 1), ("revisionId", 1), ("idempotencyKey", 1)], unique=True
            ),
            IndexModel([("state", 1), ("leaseUntil", 1), ("runId", 1)]),
            IndexModel(
                [
                    ("modelId", 1),
                    ("revisionId", 1),
                    ("resourceKind", 1),
                    ("state", 1),
                    ("isEdit", 1),
                    ("createdAt", -1),
                ]
            ),
        ]
        for collection, indexes in definitions.items():
            await self.database[collection].create_indexes(indexes)

    async def transaction(
        self, operation: Callable[[DocumentRepository], Awaitable[T]]
    ) -> T:
        async with self.client.start_session() as session:

            async def guarded(active):
                repo = MongoRepository(self.database, active)
                await self.check_transaction(repo)
                return await operation(repo)

            return await session.with_transaction(
                guarded,
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
            )

    async def ping(self):
        await self.client.admin.command("ping")

    async def close(self):
        if self.client is not None:
            await self.client.close()
            self.client = None
            self.database = None
            self._read_repository = None
