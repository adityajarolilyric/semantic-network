import asyncio
from copy import deepcopy

from backend.app.core.errors import Conflict
from backend.app.repositories.base import DocumentRepository, MetadataStore


class MemoryRepository(DocumentRepository):
    KEYS = {
        "workspace_control": ("controlId",),
        "workspace_leases": ("leaseId",),
        "semantic_models": ("modelId",),
        "semantic_revisions": ("modelId", "revisionId"),
        "semantic_objects": ("modelId", "revisionId", "objectId"),
        "semantic_evidence": ("modelId", "revisionId", "recordId"),
        "semantic_examples": ("modelId", "revisionId", "recordId"),
        "semantic_contracts": ("contractId",),
        "semantic_managed_tables": ("modelId", "revisionId", "sourceId"),
        "engine_runs": ("runId",),
        "engine_run_inputs": ("runId", "inputId"),
        "engine_run_steps": ("runId", "stepId"),
        "engine_artifacts": ("artifactId",),
        "engine_table_resources": (
            "modelId",
            "revisionId",
            "resourceKind",
            "resourceId",
        ),
        "engine_read_leases": ("readId", "artifactId"),
        "engine_artifact_pins": ("runId", "artifactId"),
        "engine_artifact_bindings": ("modelId", "revisionId", "sourceId"),
    }

    def __init__(self, store):
        self.store = store

    @staticmethod
    def matches(document, query):
        for key, value in query.items():
            actual = document.get(key)
            if isinstance(value, dict):
                if "$in" in value and actual not in value["$in"]:
                    return False
                if "$gt" in value and not actual > value["$gt"]:
                    return False
                if "$lt" in value and not actual < value["$lt"]:
                    return False
            elif isinstance(actual, list):
                if value not in actual:
                    return False
            elif actual != value:
                return False
        return True

    async def get(self, collection, query):
        return next(
            (
                deepcopy(d)
                for d in self.store.documents.get(collection, [])
                if self.matches(d, query)
            ),
            None,
        )

    async def list(
        self, collection, query, *, key, after=None, limit=100, descending=False
    ):
        docs = [
            deepcopy(d)
            for d in self.store.documents.get(collection, [])
            if self.matches(d, query) and (after is None or d[key] > after)
        ]
        return sorted(docs, key=lambda d: d[key], reverse=descending)[:limit]

    async def insert(self, collection, document):
        docs = self.store.documents.setdefault(collection, [])
        keys = self.KEYS[collection]
        if collection == "engine_artifact_bindings" and "sourceId" not in document:
            keys = ("modelId", "revisionId", "resourceKind", "resourceId")
        identity = {key: document[key] for key in keys}
        if any(self.matches(d, identity) for d in docs):
            raise Conflict("A resource with this identity already exists.")
        docs.append(deepcopy(document))
        return deepcopy(document)

    async def replace(self, collection, query, document):
        docs = self.store.documents.setdefault(collection, [])
        for i, current in enumerate(docs):
            if self.matches(current, query):
                docs[i] = deepcopy(document)
                return True
        return False

    async def delete(self, collection, query):
        docs = self.store.documents.setdefault(collection, [])
        for i, current in enumerate(docs):
            if self.matches(current, query):
                del docs[i]
                return True
        return False


class MemoryStore(MetadataStore):
    def __init__(self):
        self.documents = {}
        self.lock = asyncio.Lock()

    @property
    def repository(self):
        return MemoryRepository(self)

    async def transaction(self, operation):
        async with self.lock:
            snapshot = deepcopy(self.documents)
            try:
                await self.check_transaction(self.repository)
                return await operation(self.repository)
            except BaseException:
                self.documents = snapshot
                raise

    async def open(self):
        return None

    async def close(self):
        return None

    async def ping(self):
        return None
