"""Profile a captured managed input and return an object-local pivot suggestion."""

from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..engine.execution.inputs.managed import ManagedInputCapture
from ..warehouse.dimension_execution import DimensionTableExecutor
from ..engine.transformations.pivot.suggestions import PivotSuggestionDetector
from ..engine.execution.read_leases import ArtifactReadLeases
from .base import Service


class PivotSuggestionService(Service):
    def __init__(self, store, max_objects, managed, settings):
        super().__init__(store, max_objects)
        self.managed = managed
        self.inputs = ManagedInputCapture()
        self.leases = ArtifactReadLeases(store)
        self.detector = PivotSuggestionDetector()
        self.executor = DimensionTableExecutor(managed.executor.warehouse, settings)

    async def suggest(self, model, revision, table_id):
        read_id = "read_" + uuid4().hex

        async def capture(repo):
            source = await self.managed.source(repo, model, revision, table_id)
            snapshot = await self.inputs.capture_source(repo, model, revision, source)
            if len(snapshot.artifact["columns"]) > 200:
                raise MetadataError(
                    "pivot_suggestion_limit",
                    "Automatic suggestions support at most 200 columns.",
                )
            await self.leases.acquire(
                repo,
                read_id,
                [snapshot.artifact],
                resource="Managed table",
                retired_message="The managed table changed. Retry the suggestion.",
            )
            return source, snapshot

        source, snapshot = await self.store.transaction(capture)
        artifact = snapshot.artifact
        async with self.leases.hold(
            read_id, [artifact["artifactId"]], "Pivot suggestion read expired. Retry."
        ):
            plan = self.managed.planner.plan(
                source, self.managed.input_definition(snapshot.definition["definition"])
            )
            await self.managed.executor.verify(
                artifact, self.managed.compiler.compile(plan)
            )
            rows = await self.managed.executor.warehouse.queries.pivot_profile(
                artifact,
                self.executor.execution_settings(),
                self.detector.max_rows,
            )
            result = self.detector.suggest(table_id, artifact["columns"], rows)

            async def verify(repo):
                current_source = await self.managed.source(
                    repo, model, revision, table_id
                )
                current = await self.inputs.capture_source(
                    repo, model, revision, current_source
                )
                if (
                    current.artifact["artifactId"] != artifact["artifactId"]
                    or current.source_version != snapshot.source_version
                ):
                    raise Conflict(
                        "The managed input changed during profiling. Retry the suggestion."
                    )
                await self.leases.verify(
                    repo,
                    read_id,
                    [artifact["artifactId"]],
                    "Pivot suggestion read expired. Retry.",
                )

            await self.store.transaction(verify)
            return result
