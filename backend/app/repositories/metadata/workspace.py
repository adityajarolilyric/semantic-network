"""Workspace persistence operations and physical collection mapping."""

from .collection import CollectionRepository
from .criteria import WorkspaceControlLookup, WorkspaceLeaseLookup


class WorkspaceControlRepository(CollectionRepository):
    collection = "workspace_control"
    default_order = "controlId"
    criteria_type = WorkspaceControlLookup


class WorkspaceLeaseRepository(CollectionRepository):
    collection = "workspace_leases"
    default_order = "leaseId"
    criteria_type = WorkspaceLeaseLookup
