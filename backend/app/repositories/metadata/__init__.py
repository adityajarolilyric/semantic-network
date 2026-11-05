"""Transaction-bound domain repositories; no client creation or new sessions."""

from functools import cached_property
from ...db.schema import CollectionSchemas
from .catalog import (
    ModelRepository,
    RevisionRepository,
    ObjectRepository,
    ManagedTableRepository,
    ContractRepository,
)
from .engine import (
    RunRepository,
    ArtifactRepository,
    BindingRepository,
    RunInputRepository,
    PinRepository,
    ReadLeaseRepository,
    StepRepository,
    TableResourceRepository,
)
from .workspace import WorkspaceControlRepository, WorkspaceLeaseRepository
from .records import RecordCategory, RecordRepository
from .criteria import ObjectLookup, ManagedTableLookup, RecordLookup


class MetadataRepositories:
    def __init__(self, documents):
        self.documents = documents
        self._records = {}

    def records(self, category):
        if category not in self._records:
            self._records[category] = RecordRepository(self.documents, category)
        return self._records[category]

    async def ensure_revision_empty(self, model_id, revision_id, message):
        await self.objects.ensure_empty(
            ObjectLookup(model_id=model_id, revision_id=revision_id), message
        )
        await self.managed_tables.ensure_empty(
            ManagedTableLookup(model_id=model_id, revision_id=revision_id), message
        )
        for category in (RecordCategory.EVIDENCE, RecordCategory.EXAMPLE):
            await self.records(category).ensure_empty(
                RecordLookup(model_id=model_id, revision_id=revision_id), message
            )

    async def ensure_object_unreferenced(
        self, model_id, revision_id, object_id, message
    ):
        for category in (RecordCategory.EVIDENCE, RecordCategory.EXAMPLE):
            await self.records(category).ensure_empty(
                RecordLookup(
                    model_id=model_id,
                    revision_id=revision_id,
                    references_object_id=object_id,
                ),
                message,
            )

    async def clear_workspace(self):
        for collection in CollectionSchemas.REQUIRED:
            if collection != "workspace_control":
                await self.documents.delete_all(collection)

    @cached_property
    def activation(self):
        from .activation import ActivationWriter

        return ActivationWriter(self.documents)

    @cached_property
    def models(self):
        return ModelRepository(self.documents)

    @cached_property
    def revisions(self):
        return RevisionRepository(self.documents)

    @cached_property
    def objects(self):
        return ObjectRepository(self.documents)

    @cached_property
    def managed_tables(self):
        return ManagedTableRepository(self.documents)

    @cached_property
    def contracts(self):
        return ContractRepository(self.documents)

    @cached_property
    def runs(self):
        return RunRepository(self.documents)

    @cached_property
    def artifacts(self):
        return ArtifactRepository(self.documents)

    @cached_property
    def bindings(self):
        return BindingRepository(self.documents)

    @cached_property
    def inputs(self):
        return RunInputRepository(self.documents)

    @cached_property
    def pins(self):
        return PinRepository(self.documents)

    @cached_property
    def read_leases(self):
        return ReadLeaseRepository(self.documents)

    @cached_property
    def steps(self):
        return StepRepository(self.documents)

    @cached_property
    def table_resources(self):
        return TableResourceRepository(self.documents)

    @cached_property
    def workspace_control(self):
        return WorkspaceControlRepository(self.documents)

    @cached_property
    def workspace_leases(self):
        return WorkspaceLeaseRepository(self.documents)


def metadata_repositories(documents):
    # The document adapter owns its session. Reuse within that exact adapter,
    # never across transaction attempts or concurrent MongoDB sessions.
    repositories = vars(documents).get("_domain_repositories")
    if repositories is None:
        repositories = MetadataRepositories(documents)
        documents._domain_repositories = repositories
    return repositories
