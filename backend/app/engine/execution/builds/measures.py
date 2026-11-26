"""Coordinate preparation, per-population materialisation and fenced activation."""

from .contracts import MeasureBuildCandidate
from .edit_cells import EditCellStage
from .measure_activation import MeasureBuildActivation
from .measure_population import MeasurePopulationBuilder
from .measure_preparation import MeasureBuildPreparation


class MeasureBuildHandler:
    def __init__(self, builds):
        self.builds = builds
        self.edit_cells = EditCellStage(builds)
        self.preparation = MeasureBuildPreparation(builds)
        self.population_builder = MeasurePopulationBuilder(builds)
        self.activation = MeasureBuildActivation(builds)

    async def execute_run(self, run, worker):
        prepared = await self.preparation.prepare(run, worker)
        if prepared is None:
            return  # An unchanged incremental build has already been reused.
        async with self.edit_cells.hold(run, worker, prepared.compiler):
            outcomes = []
            for population_id in prepared.population_order(run["resourceId"]):
                outcomes.append(
                    await self.population_builder.build(
                        run, worker, prepared, population_id
                    )
                )
            candidate = MeasureBuildCandidate(
                outcomes, prepared.populations, prepared.snapshots, prepared.previous
            )
            await self.activation.activate(run, worker, candidate)
