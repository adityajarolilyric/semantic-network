from ....core.errors import Conflict, ImmutableRevision
from ....core.metadata import revision_scope as scope
from ....core.metadata import utc_now as now
from ....repositories.metadata import metadata_repositories
from ....repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    RunInputLookup,
)
from .contracts import MeasureBuildCandidate
from .source_schema_commit import SourceSchemaCommit


class MeasureBuildActivation:
    def __init__(self, builds):
        self.builds = builds
        self.source_schema = SourceSchemaCommit(builds)

    async def activate(self, run, worker, candidate: MeasureBuildCandidate):
        builds = self.builds
        outcomes, snapshots, previous = (
            candidate.artifacts,
            candidate.snapshots,
            candidate.previous,
        )

        async def activate(repo):
            current = await worker.fenced(repo, run)
            (
                revision_document,
                _,
                _,
                _,
                _,
                _,
                fingerprint,
            ) = await builds.capture_build_inputs(
                repo, run["modelId"], run["revisionId"]
            )
            builds.check_version(revision_document, run["revisionVersion"])
            if revision_document["approvalStatus"] != "draft":
                raise ImmutableRevision()
            if fingerprint != run["inputHash"]:
                raise Conflict("Measure inputs changed during the build.")
            if run.get("buildMode") == "incremental":
                await builds.incremental.check_snapshots(repo, run, snapshots)
            previous_binding = await metadata_repositories(repo).bindings.find(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    resource_kind="measureValues",
                    resource_id=run["resourceId"],
                )
            )
            if (previous_binding or {}).get("generationId") != run.get(
                "previousGenerationId", (previous or {}).get("generationId")
            ):
                raise Conflict(
                    "Another value generation was activated. Refresh and retry."
                )
            published_hash = await self.source_schema.apply(repo, run, outcomes)
            for artifact in outcomes:
                identity = {
                    **scope(run["modelId"], run["revisionId"]),
                    "resourceKind": "measureValues",
                    "resourceId": artifact["levelId"],
                }
                binding = {
                    **identity,
                    "artifactId": artifact["artifactId"],
                    "definitionHash": published_hash,
                    "inputHash": published_hash,
                    "generationId": run["runId"],
                    "builtAt": now(),
                }
                await metadata_repositories(repo).activation.bind(identity, binding)
                await metadata_repositories(repo).artifacts.replace(
                    ArtifactLookup(artifact_id=artifact["artifactId"]), artifact
                )
            old_bindings = await metadata_repositories(repo).bindings.list(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    resource_kind="measureValues",
                ),
                order_by="resourceId",
                limit=builds.max_objects + 1,
            )
            if len(old_bindings) > builds.max_objects:
                raise Conflict("Measure bindings exceed the metadata object limit.")
            for old in old_bindings:
                if old["resourceId"] not in {
                    artifact["levelId"] for artifact in outcomes
                }:
                    await metadata_repositories(repo).bindings.delete(
                        BindingLookup(
                            model_id=run["modelId"],
                            revision_id=run["revisionId"],
                            resource_kind="measureValues",
                            resource_id=old["resourceId"],
                        )
                    )
            if run.get("isEdit"):
                await metadata_repositories(repo).inputs.delete(
                    RunInputLookup(run_id=run["runId"], input_id="__edit")
                )
            await builds.touch_revision(repo, run["modelId"], run["revisionId"])
            await metadata_repositories(repo).activation.complete(
                run, current, generationId=run["runId"]
            )

        await builds.storage.activate(
            outcomes, lambda: builds.store.transaction(activate)
        )
