"""Named access to immutable job inputs, including existing persisted formats."""

from dataclasses import dataclass
from typing import Any, NamedTuple
from functools import cached_property

from .grain_contexts import resolve_grain_contexts

from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import RunInputLookup

Document = dict[str, Any]


class MeasureBuildInputs(NamedTuple):
    revision: Document
    objects: list[Document]
    populations: dict[str, Document]
    managed_artifacts: dict[str, Document]
    managed_definitions: dict[str, Document]
    bindings: dict[str, Document]
    input_hash: str


@dataclass(frozen=True)
class CapturedRunInputs:
    documents: tuple[Document, ...]

    @classmethod
    async def load(cls, repository, run_id, limit):
        return cls(
            tuple(
                await metadata_repositories(repository).inputs.list(
                    RunInputLookup(run_id=run_id), order_by="inputId", limit=limit
                )
            )
        )

    @property
    def objects(self):
        return [
            item["document"]
            for item in self.documents
            if not item["inputId"].startswith("__")
        ]

    @cached_property
    def graph(self):
        return resolve_grain_contexts(self.objects)

    def group(self, prefix):
        return {
            item["inputId"].removeprefix(prefix): item["document"]
            for item in self.documents
            if item["inputId"].startswith(prefix)
        }

    def single(self, input_id):
        return next(
            (
                item["document"]
                for item in self.documents
                if item["inputId"] == input_id
            ),
            None,
        )

    @property
    def populations(self):
        return self.group("__population_")

    @property
    def managed_artifacts(self):
        return {
            source_id: captured["artifact"]
            for source_id, captured in self.group("__managed_").items()
        }

    @property
    def managed_definitions(self):
        return {
            source_id: captured["definition"]
            for source_id, captured in self.group("__managed_").items()
        }

    @property
    def value_snapshots(self):
        return self.group("__values_")

    @property
    def materialisation_manifests(self):
        return self.group("__manifest_")

    @property
    def relations(self):
        return {
            document["resourceId"]: document["relation"]
            for document in self.group("__relation_").values()
        }

    @property
    def relation_definitions(self):
        return {
            document["resourceId"]: document["managed"]
            for document in self.group("__relation_").values()
            if document["managed"]
        }
