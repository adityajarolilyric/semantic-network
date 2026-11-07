from dataclasses import dataclass

from .core.config import Settings
from .engine.semantic_network import SemanticNetworkHandler
from .engine.managed_builds import ManagedTableBuildHandler
from .engine.artifacts import ArtifactCollector
from .engine.worker import EngineWorker
from .repositories.base import MetadataStore
from .services.compatibility import CompatibilityService
from .services.contracts import ContractService
from .services.decision_view import DecisionViewService
from .services.dimension_builds import DimensionBuildService
from .services.dimension_members import DimensionMembersService
from .services.dimensions import DimensionService
from .services.dimension_commands import DimensionCommandService
from .services.evidence import EvidenceService
from .services.examples import ExampleService
from .services.managed_tables import ManagedTableService
from .services.pivot_suggestions import PivotSuggestionService
from .services.measure_builds import MeasureBuildService
from .services.measures import MeasureService
from .services.models import ModelService
from .services.revisions import RevisionService
from .services.semantic_network import SemanticNetworkService
from .services.source_registration import SourceRegistrationService
from .services.sources import SourceService
from .services.transaction_grains import TransactionGrainService
from .services.transaction_measures import TransactionMeasureFactory
from .services.transaction_resolution import TransactionResolutionService
from .services.transactions import TransactionService
from .services.upload_metadata import UploadMetadataService
from .services.workspace import WorkspaceService
from .services.workspace_gate import WorkspaceGate
from .services.workspace_reset import WorkspaceResetService
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
        self.dimension_members = DimensionMembersService(*args, self.dimensions)
        self.measures = MeasureService(*args)
        self.transaction_measure_factory = TransactionMeasureFactory(self.measures)
        self.transactions = TransactionService(
            *args, measure_factory=self.transaction_measure_factory
        )
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
        self.pivot_suggestions = PivotSuggestionService(
            *args, self.managed_tables, self.settings
        )
        self.upload_metadata = UploadMetadataService(
            *args, self.workspace, self.managed_tables, self.transactions
        )
        self.warehouse_sources.upload_metadata = self.upload_metadata
        self.revisions.managed_tables = self.managed_tables
        self.dimension_builds = DimensionBuildService(
            self.store, self.settings, self.managed_tables
        )
        self.revisions.dimension_builds = self.dimension_builds
        self.measure_builds = MeasureBuildService(
            self.store, self.settings, self.dimension_builds, self.managed_tables
        )
        self.semantic_network = SemanticNetworkService(
            self.store,
            self.settings,
            self.dimension_builds,
            self.measure_builds,
            self.managed_tables,
        )
        network_handler = SemanticNetworkHandler(self.semantic_network)
        self.engine_worker = EngineWorker(
            self.store,
            handlers={
                "managedTable": ManagedTableBuildHandler(self.managed_tables),
                "dimension": self.dimension_builds.handler,
                "dimensionPreview": self.dimension_builds.handler,
                "measureSet": self.measure_builds.handler,
                "semanticNetwork": network_handler,
                "dimensionSet": network_handler,
            },
            collector=ArtifactCollector(
                self.managed_tables, self.dimension_builds.storage
            ),
            workspace_gate=self.workspace_gate,
        )
        self.dimension_commands = DimensionCommandService(
            *args, self.dimensions, self.semantic_network
        )
        self.transaction_resolution = TransactionResolutionService(
            self.store, self.settings, self.dimension_builds, self.managed_tables
        )
        self.transaction_grains = TransactionGrainService(
            self.transactions, self.transaction_resolution
        )
        self.decision_view = DecisionViewService(
            self.store, self.settings, self.measure_builds
        )
        self.compatibility = CompatibilityService(
            self.store, self.settings, self.decision_view
        )
