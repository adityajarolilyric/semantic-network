from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...measures.compiler import MeasureCompiler


@dataclass(frozen=True)
class MeasureBuildCandidate:
    artifacts: list[dict]
    populations: dict
    snapshots: dict
    previous: dict | None


@dataclass(frozen=True)
class PreparedMeasureBuild:
    """Captured, verified inputs and one operation-local compiler for the build."""

    objects: list
    populations: dict
    snapshots: dict
    previous: dict | None
    manifests: dict
    compiler: "MeasureCompiler"

    def population_order(self, primary_id):
        return [
            primary_id,
            *(
                population_id
                for population_id in self.manifests
                if population_id != primary_id
            ),
        ]
