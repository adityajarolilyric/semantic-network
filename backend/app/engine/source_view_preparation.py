"""Prepare changed source-view names through ordinary durable managed builds."""

from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ManagedTableLookup,
)
from .contracts import digest
from .source_view_names import has_readable_name


class SourceViewPreparation:
    def __init__(self, managed):
        self.managed = managed

    async def next_build(self, run, objects):
        repositories = metadata_repositories(self.managed.store.repository)
        for source in sorted(
            (
                o
                for o in objects
                if o["kind"] == "source" and o["definition"]["inScope"]
            ),
            key=lambda obj: obj["objectId"],
        ):
            source_id = source["objectId"]
            binding = await repositories.bindings.require(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    source_id=source_id,
                ),
                "Managed source view",
            )
            artifact = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Managed source view"
            )
            if has_readable_name(artifact, source, objects):
                continue
            definition = await repositories.managed_tables.require(
                ManagedTableLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    source_id=source_id,
                ),
                "Managed source definition",
            )
            return await self.managed.submit(
                run["modelId"],
                run["revisionId"],
                source_id,
                self.managed.input_definition(definition["definition"]),
                definition["version"],
                "readable_view_" + digest([run["runId"], source_id])[:40],
                repair=True,
            )
        return None
