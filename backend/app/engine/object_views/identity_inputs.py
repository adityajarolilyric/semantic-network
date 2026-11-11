"""Retire redundant identity wrappers while retaining managed input metadata."""

from ...repositories.metadata import metadata_repositories
from ...repositories.metadata.criteria import ArtifactLookup, ManagedTableLookup
from ..execution.builds.source_input_commit import SourceInputCommit


class IdentityInputReferences:
    def __init__(self):
        self.committer = SourceInputCommit()

    async def capture(self, repo, model, revision, tokens, objects):
        repositories = metadata_repositories(repo)
        sources = {o["objectId"]: o for o in objects if o["kind"] == "source"}
        references = []
        for source_id, aid in tokens["sourceInputs"].items():
            definition = await repositories.managed_tables.require(
                ManagedTableLookup(
                    model_id=model, revision_id=revision, source_id=source_id
                ),
                "Managed input",
            )
            artifact = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=aid), "Managed input artifact"
            )
            if (
                definition["definition"].get("pipeline")
                or artifact.get("storageMode") == "source_input"
            ):
                continue
            references.append(
                {
                    "sourceId": source_id,
                    "sourceTableName": sources[source_id]["definition"]["tableName"],
                    "retiredInputView": {
                        "artifactId": aid,
                        "physicalName": artifact["physicalName"],
                    },
                }
            )
        return references

    async def apply(self, repo, run, references):
        await self.committer.apply(repo, run, references)
