"""Engine persistence operations and physical collection mapping."""

from .collection import CollectionRepository
from .criteria import (
    RunLookup,
    ArtifactLookup,
    BindingLookup,
    RunInputLookup,
    PinLookup,
    ReadLeaseLookup,
    StepLookup,
    TableResourceLookup,
)


class RunRepository(CollectionRepository):
    collection = "engine_runs"
    default_order = "runId"
    criteria_type = RunLookup


class ArtifactRepository(CollectionRepository):
    collection = "engine_artifacts"
    default_order = "artifactId"
    criteria_type = ArtifactLookup


class BindingRepository(CollectionRepository):
    collection = "engine_artifact_bindings"
    default_order = "resourceId"
    criteria_type = BindingLookup


class RunInputRepository(CollectionRepository):
    collection = "engine_run_inputs"
    default_order = "inputId"
    criteria_type = RunInputLookup


class PinRepository(CollectionRepository):
    collection = "engine_artifact_pins"
    default_order = "artifactId"
    criteria_type = PinLookup


class ReadLeaseRepository(CollectionRepository):
    collection = "engine_read_leases"
    default_order = "artifactId"
    criteria_type = ReadLeaseLookup


class StepRepository(CollectionRepository):
    collection = "engine_run_steps"
    default_order = "stepId"
    criteria_type = StepLookup


class TableResourceRepository(CollectionRepository):
    collection = "engine_table_resources"
    default_order = "physicalName"
    criteria_type = TableResourceLookup
