"""Typed domain lookups. MongoDB operators and field translation stay here."""

from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum


class Unset(Enum):
    VALUE = "unset"


UNSET = Unset.VALUE


@dataclass(frozen=True)
class Criteria:
    @classmethod
    def from_record(cls, record):
        values = {}
        for definition in fields(cls):
            key = definition.metadata["field"]
            if not definition.metadata.get("operator") and key in record:
                value = record[key]
                if isinstance(value, dict):
                    raise TypeError(
                        "A metadata identity cannot contain database operators"
                    )
                values[definition.name] = value
        return cls(**values)

    def mongo_selector(self):
        selector = {}
        for definition in fields(self):
            value = getattr(self, definition.name)
            if value is UNSET:
                continue
            key = definition.metadata["field"]
            operator = definition.metadata.get("operator")
            if isinstance(value, dict):
                raise TypeError("Lookups accept domain values, not database operators")
            if operator == "$in" and not isinstance(value, (tuple, list)):
                raise TypeError("A membership lookup requires a list or tuple")
            selector[key] = (
                {operator: list(value) if operator == "$in" else value}
                if operator
                else value
            )
        return selector


def lookup_field(name, operator=None):
    return field(default=UNSET, metadata={"field": name, "operator": operator})


@dataclass(frozen=True)
class ModelLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    version: int | Unset = lookup_field("version")


@dataclass(frozen=True)
class RevisionLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    version: int | Unset = lookup_field("version")
    approval_status: str | Unset = lookup_field("approvalStatus")


@dataclass(frozen=True)
class ObjectLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    object_id: str | Unset = lookup_field("objectId")
    kind: str | Unset = lookup_field("kind")
    version: int | Unset = lookup_field("version")


@dataclass(frozen=True)
class ManagedTableLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    source_id: str | Unset = lookup_field("sourceId")
    version: int | Unset = lookup_field("version")


@dataclass(frozen=True)
class RecordLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    record_id: str | Unset = lookup_field("recordId")
    references_object_id: str | Unset = lookup_field("objectIds")
    version: int | Unset = lookup_field("version")


@dataclass(frozen=True)
class ContractLookup(Criteria):
    contract_id: str | Unset = lookup_field("contractId")
    version: int | Unset = lookup_field("version")


@dataclass(frozen=True)
class RunLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    run_id: str | Unset = lookup_field("runId")
    idempotency_key: str | Unset = lookup_field("idempotencyKey")
    resource_kind: str | Unset = lookup_field("resourceKind")
    resource_id: str | Unset = lookup_field("resourceId")
    source_id: str | Unset = lookup_field("sourceId")
    state: str | Unset = lookup_field("state")
    is_edit: bool | None | Unset = lookup_field("isEdit")
    fence: int | Unset = lookup_field("fence")
    owner: str | Unset = lookup_field("owner")
    lease_until: datetime | Unset = lookup_field("leaseUntil")
    version: int | Unset = lookup_field("version")
    state_in: tuple[str | bool | None, ...] | Unset = lookup_field("state", "$in")
    is_edit_in: tuple[str | bool | None, ...] | Unset = lookup_field("isEdit", "$in")
    lease_until_before: datetime | Unset = lookup_field("leaseUntil", "$lt")


@dataclass(frozen=True)
class ArtifactLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    resource_kind: str | Unset = lookup_field("resourceKind")
    resource_id: str | Unset = lookup_field("resourceId")
    source_id: str | Unset = lookup_field("sourceId")
    artifact_id: str | Unset = lookup_field("artifactId")
    run_id: str | Unset = lookup_field("runId")
    state: str | Unset = lookup_field("state")
    created_at: datetime | Unset = lookup_field("createdAt")
    pin_version: int | Unset = lookup_field("pinVersion")
    state_in: tuple[str | bool | None, ...] | Unset = lookup_field("state", "$in")
    created_before: datetime | Unset = lookup_field("createdAt", "$lt")


@dataclass(frozen=True)
class BindingLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    resource_kind: str | Unset = lookup_field("resourceKind")
    resource_id: str | Unset = lookup_field("resourceId")
    source_id: str | Unset = lookup_field("sourceId")
    artifact_id: str | Unset = lookup_field("artifactId")


@dataclass(frozen=True)
class RunInputLookup(Criteria):
    run_id: str | Unset = lookup_field("runId")
    input_id: str | Unset = lookup_field("inputId")


@dataclass(frozen=True)
class PinLookup(Criteria):
    run_id: str | Unset = lookup_field("runId")
    artifact_id: str | Unset = lookup_field("artifactId")


@dataclass(frozen=True)
class ReadLeaseLookup(Criteria):
    read_id: str | Unset = lookup_field("readId")
    artifact_id: str | Unset = lookup_field("artifactId")
    expires_at: datetime | Unset = lookup_field("expiresAt")
    expires_after: datetime | Unset = lookup_field("expiresAt", "$gt")


@dataclass(frozen=True)
class StepLookup(Criteria):
    run_id: str | Unset = lookup_field("runId")
    step_id: str | Unset = lookup_field("stepId")


@dataclass(frozen=True)
class TableResourceLookup(Criteria):
    model_id: str | Unset = lookup_field("modelId")
    revision_id: str | Unset = lookup_field("revisionId")
    kind: str | Unset = lookup_field("kind")
    resource_kind: str | Unset = lookup_field("resourceKind")
    resource_id: str | Unset = lookup_field("resourceId")
    physical_name: str | Unset = lookup_field("physicalName")
    state: str | Unset = lookup_field("state")
    storage_owner: str | Unset = lookup_field("storageOwner")


@dataclass(frozen=True)
class WorkspaceControlLookup(Criteria):
    control_id: str | Unset = lookup_field("controlId")


@dataclass(frozen=True)
class WorkspaceLeaseLookup(Criteria):
    lease_id: str | Unset = lookup_field("leaseId")
    epoch: int | Unset = lookup_field("epoch")
    expires_at: datetime | Unset = lookup_field("expiresAt")
    expires_after: datetime | Unset = lookup_field("expiresAt", "$gt")
