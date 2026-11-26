"""Keep an identity managed input as metadata over the imported physical table."""

from ....core.errors import Conflict
from ....repositories.metadata import metadata_repositories
from ....repositories.metadata.criteria import ArtifactLookup, BindingLookup


class SourceInputCommit:
    async def apply(self, repo, run, outcomes):
        repositories = metadata_repositories(repo)
        changed = False
        for output in outcomes:
            retired = output.get("retiredInputView")
            if not retired:
                continue
            binding = await repositories.bindings.require(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    source_id=output["sourceId"],
                ),
                "Managed input binding",
            )
            if binding["artifactId"] != retired["artifactId"]:
                raise Conflict("The managed input changed during this build.")
            artifact = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=retired["artifactId"]), "Managed input"
            )
            await repositories.artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {
                    **artifact,
                    "physicalName": output["sourceTableName"],
                    "sourceTableName": output["sourceTableName"],
                    "kind": "source",
                    "storageMode": "source_input",
                },
            )
            changed = True
        return changed
