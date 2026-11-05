"""Catalog persistence operations and physical collection mapping."""

from .collection import CollectionRepository
from .criteria import (
    ModelLookup,
    RevisionLookup,
    ObjectLookup,
    ManagedTableLookup,
    ContractLookup,
)


class ModelRepository(CollectionRepository):
    collection = "semantic_models"
    default_order = "modelId"
    criteria_type = ModelLookup


class RevisionRepository(CollectionRepository):
    collection = "semantic_revisions"
    default_order = "revisionId"
    criteria_type = RevisionLookup


class ObjectRepository(CollectionRepository):
    collection = "semantic_objects"
    default_order = "objectId"
    criteria_type = ObjectLookup


class ManagedTableRepository(CollectionRepository):
    collection = "semantic_managed_tables"
    default_order = "sourceId"
    criteria_type = ManagedTableLookup


class ContractRepository(CollectionRepository):
    collection = "semantic_contracts"
    default_order = "contractId"
    criteria_type = ContractLookup
