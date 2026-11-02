from .services.transaction_resolution import TransactionResolutionService
from .services.planning_view import PlanningViewService
from .services.workspace_gate import WorkspaceGate
from .services.workspace_reset import WorkspaceResetService
from dataclasses import dataclass

from .engine.worker import EngineWorker
from .services.planning_builds import PlanningBuildService
from .services.measure_builds import MeasureBuildService
from .services.managed_tables import ManagedTableService
from .core.config import Settings
from .repositories.base import MetadataStore
from .services.contracts import ContractService
from .services.dimensions import DimensionService
from .services.evidence import EvidenceService
from .services.examples import ExampleService
from .services.measures import MeasureService
from .services.models import ModelService
from .services.planning_levels import PlanningLevelService
from .services.revisions import RevisionService
from .services.source_registration import SourceRegistrationService
from .services.sources import SourceService
from .services.transactions import TransactionService
from .services.workspace import WorkspaceService
from .warehouse.repository import WarehouseRepository
from .warehouse.service import WarehouseSourceService


@dataclass
class ServiceContainer:
    store: MetadataStore
    settings: Settings
    warehouse_repository: WarehouseRepository

    def __post_init__(self):
        self.workspace_gate = WorkspaceGate(self.store)
        args = (self.store, self.settings.max_objects_per_revision)
        self.models = ModelService(*args)
        self.workspace = WorkspaceService(*args)
        self.workspace_reset = WorkspaceResetService(
            self.store, self.warehouse_repository, self.workspace, self.workspace_gate
        )
        self.revisions = RevisionService(*args)
        self.sources = SourceService(*args)
        self.dimensions = DimensionService(*args)
        self.planning_levels = PlanningLevelService(*args)
        self.transactions = TransactionService(*args)
        self.measures = MeasureService(*args)
        self.evidence = EvidenceService(*args)
        self.examples = ExampleService(*args)
        self.contracts = ContractService(*args)
        self.warehouse_sources = WarehouseSourceService(
            self.warehouse_repository, self.settings
        )
        self.source_registration = SourceRegistrationService(
            self.sources, self.warehouse_sources
        )

        self.managed_tables = ManagedTableService(
            self.store,
            self.settings,
            self.warehouse_repository,
            self.source_registration,
        )
        self.managed_tables.workspace_gate = self.workspace_gate
        self.engine_worker = EngineWorker(self.managed_tables)
        self.revisions.managed_tables = self.managed_tables
        self.planning_builds = PlanningBuildService(
            self.store, self.settings, self.managed_tables
        )
        self.managed_tables.planning_builds = self.planning_builds
        self.revisions.planning_builds = self.planning_builds
        self.measure_builds = MeasureBuildService(
            self.store, self.settings, self.planning_builds, self.managed_tables
        )
        self.managed_tables.measure_builds = self.measure_builds
        self.transaction_resolution = TransactionResolutionService(
            self.store, self.settings, self.planning_builds, self.managed_tables
        )
        self.planning_view = PlanningViewService(
            self.store, self.settings, self.measure_builds
        )
