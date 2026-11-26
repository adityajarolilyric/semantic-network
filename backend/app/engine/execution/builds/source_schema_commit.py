"""Commit direct input references and type widening with fenced publication."""

from ....core.metadata import utc_now
from ....repositories.metadata import metadata_repositories
from ....repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ManagedTableLookup,
    ObjectLookup,
    RunLookup,
)
from ...sql.contracts import digest
from .incremental import MaterialisationManifest
from .source_input_commit import SourceInputCommit


class SourceSchemaCommit:
    def __init__(self, builds):
        self.builds = builds
        self.inputs = SourceInputCommit()

    async def apply(self, repo, run, outcomes):
        repositories = metadata_repositories(repo)
        changed = await self.inputs.apply(repo, run, outcomes)
        for artifact in outcomes:
            promotions = artifact.get("sourcePromotions")
            if not promotions:
                continue
            identity = dict(
                model_id=run["modelId"],
                revision_id=run["revisionId"],
                object_id=artifact["sourceId"],
            )
            source = await repositories.objects.require(
                ObjectLookup(**identity), "Source"
            )
            columns = [
                {**column, **promotions.get(column["columnId"], {})}
                for column in source["definition"]["columns"]
            ]
            if columns == source["definition"]["columns"]:
                continue
            changed = True
            updated = {
                **source,
                "version": source["version"] + 1,
                "updatedAt": utc_now(),
                "definition": {**source["definition"], "columns": columns},
            }
            await repositories.objects.replace(ObjectLookup(**identity), updated)
            lookup = ManagedTableLookup(
                model_id=run["modelId"],
                revision_id=run["revisionId"],
                source_id=artifact["sourceId"],
            )
            managed = await repositories.managed_tables.require(
                lookup, "Managed source"
            )
            plan = self.builds.managed.planner.plan(
                updated, self.builds.managed.input_definition(managed["definition"])
            )
            definition_hash = digest(plan.definition)
            await repositories.managed_tables.replace(
                lookup,
                {
                    **managed,
                    "version": managed["version"] + 1,
                    "definition": plan.definition,
                    "definitionHash": definition_hash,
                    "updatedAt": utc_now(),
                },
            )
            binding_lookup = BindingLookup(
                model_id=run["modelId"],
                revision_id=run["revisionId"],
                source_id=artifact["sourceId"],
            )
            binding = await repositories.bindings.require(
                binding_lookup, "Managed source binding"
            )
            await repositories.bindings.replace(
                binding_lookup, {**binding, "definitionHash": definition_hash}
            )
            managed_artifact = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Managed source view"
            )
            await repositories.artifacts.replace(
                ArtifactLookup(artifact_id=managed_artifact["artifactId"]),
                {
                    **managed_artifact,
                    "columns": list(plan.schema.columns),
                    "definitionHash": definition_hash,
                },
            )
        if not changed:
            return run["inputHash"]
        inputs = await self.builds.capture_build_inputs(
            repo, run["modelId"], run["revisionId"]
        )
        manifests = MaterialisationManifest(
            inputs.objects,
            inputs.populations,
            inputs.managed_artifacts,
            inputs.revision["baseDimensionId"],
        ).all()
        for artifact in outcomes:
            artifact.update(
                inputHash=inputs.input_hash,
                materialisationManifest=manifests[artifact["levelId"]],
                populationArtifactId=inputs.populations[artifact["levelId"]][
                    "artifactId"
                ],
            )
        if run.get("networkRunId"):
            lookup = RunLookup(run_id=run["networkRunId"])
            parent = await repositories.runs.require(lookup, "Network build")
            fingerprint = digest(
                [
                    inputs.revision["baseDimensionId"],
                    sorted(
                        (item["objectId"], item["version"]) for item in inputs.objects
                    ),
                ]
            )
            await repositories.runs.replace(
                lookup, {**parent, "definitionFingerprint": fingerprint}
            )
        return inputs.input_hash
