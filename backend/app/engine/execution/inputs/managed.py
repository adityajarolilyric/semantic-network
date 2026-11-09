"""Verify captured managed inputs without resolving newer active bindings."""

from dataclasses import dataclass

from ....core.errors import Conflict
from ....repositories.metadata import metadata_repositories
from ....repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ManagedTableLookup,
)


class ManagedInputVerifier:
    def __init__(self, managed_tables):
        self.managed_tables = managed_tables

    async def verify(self, objects, artifacts, definitions):
        objects_by_id = {document["objectId"]: document for document in objects}
        service = self.managed_tables
        for source_id, artifact in artifacts.items():
            source = objects_by_id[source_id]
            await service.executor.inspect(source)
            plan = service.planner.plan(
                source, service.input_definition(definitions[source_id]["definition"])
            )
            compiled = service.compiler.compile(plan)
            await service.executor.verify(artifact, compiled)
            await service.executor.checks(compiled)


@dataclass(frozen=True)
class ManagedSourceSnapshot:
    artifact: dict
    definition: dict
    binding: dict
    source_version: int


class ManagedInputCapture:
    """Resolve one ready source directly, without inventing a dimension."""

    async def capture_source(self, repository, model_id, revision_id, source):
        if not source["definition"]["inScope"]:
            raise Conflict("A population or dimension source is outside managed scope.")
        definition = await metadata_repositories(repository).managed_tables.require(
            ManagedTableLookup(
                model_id=model_id, revision_id=revision_id, source_id=source["objectId"]
            ),
            "Initialized managed definition",
        )
        binding = await metadata_repositories(repository).bindings.require(
            BindingLookup(
                model_id=model_id, revision_id=revision_id, source_id=source["objectId"]
            ),
            "Ready managed view",
        )
        if binding["definitionHash"] != definition["definitionHash"]:
            raise Conflict("Build the input managed view first.")
        artifact = await metadata_repositories(repository).artifacts.require(
            ArtifactLookup(artifact_id=binding["artifactId"]), "Managed artifact"
        )
        if artifact["state"] != "ready":
            raise Conflict("The input managed view is not ready.")
        return ManagedSourceSnapshot(
            {**artifact, "columns": definition["definition"]["columns"]},
            definition,
            binding,
            source["version"],
        )
