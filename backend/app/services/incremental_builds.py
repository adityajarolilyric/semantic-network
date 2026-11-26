"""Capture and atomically reuse a coherent values generation without source reload."""

from ..core.errors import Conflict, MetadataError
from ..domain.measure_policies import MEASURE_TYPE_POLICY
from ..engine.execution.inputs.captured import CapturedRunInputs
from ..engine.execution.builds.incremental import (
    INCREMENTAL_POLICY,
    MaterialisationManifest,
    reusable,
)
from ..engine.measures.contracts import (
    LEGACY_MEASURE_COMPILER_VERSION,
    MEASURE_COMPILER_VERSION,
    TYPED_MEASURE_COMPILER_VERSION,
)
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ArtifactLookup, BindingLookup, RunLookup


class IncrementalBuildCoordinator:
    def __init__(self, builds):
        self.builds = builds

    @staticmethod
    def validate_runtime_policy(run):
        if run.get("incrementalPolicy") != INCREMENTAL_POLICY or (
            run.get("buildMode") == "incremental"
            and run.get("incrementalCompilerVersion") != MEASURE_COMPILER_VERSION
        ):
            raise Conflict(
                "The incremental engine changed after this job was submitted. Resubmit the build; existing values were preserved."
            )

    async def baseline(self, repo, artifact):
        if artifact.get("numericPolicy") != MEASURE_TYPE_POLICY or artifact.get(
            "compilerVersion"
        ) not in {
            MEASURE_COMPILER_VERSION,
            LEGACY_MEASURE_COMPILER_VERSION,
            TYPED_MEASURE_COMPILER_VERSION,
        }:
            raise Conflict(
                "A Full Refresh is required to establish a compatible incremental baseline. Existing values were preserved."
            )
        manifest = artifact.get("materialisationManifest")
        if manifest and manifest.get("policy") == INCREMENTAL_POLICY:
            return manifest
        # Reconstruct a pre-upgrade baseline only from its immutable captured inputs.
        run = await metadata_repositories(repo).runs.find(
            RunLookup(run_id=artifact["runId"])
        )
        captured_inputs = await CapturedRunInputs.load(
            repo, artifact["runId"], self.builds.max_objects * 6 + 3
        )
        objects = captured_inputs.objects
        populations = captured_inputs.populations
        managed = captured_inputs.managed_artifacts
        if not run or not objects or artifact["levelId"] not in populations:
            raise Conflict(
                "Captured inputs for incremental reuse are unavailable. Use Full Refresh; no existing values were changed."
            )
        try:
            manifests = MaterialisationManifest(
                objects,
                populations,
                managed,
                run.get("baseDimensionId", run["resourceId"]),
            ).all()
        except (MetadataError, KeyError) as error:
            raise Conflict(
                "Captured incremental inputs are incomplete. Use Full Refresh; existing values were preserved."
            ) from error
        return manifests[artifact["levelId"]]

    async def prepare(
        self, repo, model, revision, base, populations, manifests, previous
    ):
        snapshots = {}
        if previous:
            for lid in manifests:
                binding = await metadata_repositories(repo).bindings.find(
                    BindingLookup(
                        model_id=model,
                        revision_id=revision,
                        resource_kind="measureValues",
                        resource_id=lid,
                    )
                )
                if not binding:
                    continue
                artifact = await metadata_repositories(repo).artifacts.require(
                    ArtifactLookup(artifact_id=binding["artifactId"]), "Current values"
                )
                if (
                    artifact["state"] != "ready"
                    or artifact.get("generationId") != previous["generationId"]
                ):
                    raise Conflict(
                        "Value generations are inconsistent. Refresh the network before incremental reuse."
                    )
                snapshots[lid] = {
                    **artifact,
                    "materialisationManifest": await self.baseline(repo, artifact),
                }
        active = await metadata_repositories(repo).bindings.list(
            BindingLookup(
                model_id=model, revision_id=revision, resource_kind="measureValues"
            ),
            order_by="resourceId",
            limit=self.builds.max_objects + 1,
        )
        reuse_only = (
            {binding["resourceId"] for binding in active} == set(manifests)
            and bool(snapshots)
            and set(snapshots) == set(manifests)
            and all(
                snapshots[lid]["materialisationManifest"] == manifests[lid]
                and (
                    manifests[lid].get("storageMode") != "source"
                    or snapshots[lid].get("storageMode") == "source"
                )
                and snapshots[lid]["populationArtifactId"]
                == populations[lid]["artifactId"]
                for lid in manifests
            )
        )
        dirty_references = set()
        for lid, manifest in manifests.items():
            for mid in manifest["evaluations"]:
                if not reusable(snapshots.get(lid, {}), manifest, mid):
                    dirty_references.add(mid)
        return snapshots, reuse_only, dirty_references

    async def check_snapshots(self, repo, run, snapshots):
        for lid, artifact in snapshots.items():
            binding = await metadata_repositories(repo).bindings.find(
                BindingLookup(
                    model_id=run["modelId"],
                    revision_id=run["revisionId"],
                    resource_kind="measureValues",
                    resource_id=lid,
                )
            )
            if not binding or binding["artifactId"] != artifact["artifactId"]:
                raise Conflict(
                    "Values changed during the incremental build. Refresh and retry."
                )

    async def reuse(self, run, worker, snapshots, manifests):
        await worker.step(run, "reuse_current_values", "running")
        await self.builds.storage.ensure_inputs(list(snapshots.values()))
        await worker.step(
            run,
            "reuse_current_values",
            "succeeded",
            rowCount=sum(a["rowCount"] for a in snapshots.values()),
        )

        async def activate(repo):
            current = await worker.fenced(repo, run)
            rev, *_, fingerprint = await self.builds.capture_build_inputs(
                repo, run["modelId"], run["revisionId"]
            )
            self.builds.check_version(rev, run["revisionVersion"])
            if rev["approvalStatus"] != "draft":
                raise Conflict("Published revisions cannot be rebuilt.")
            if fingerprint != run["inputHash"]:
                raise Conflict("Inputs changed during incremental reuse.")
            await self.check_snapshots(repo, run, snapshots)
            for lid, artifact in snapshots.items():
                binding = await metadata_repositories(repo).bindings.find(
                    BindingLookup(
                        model_id=run["modelId"],
                        revision_id=run["revisionId"],
                        resource_kind="measureValues",
                        resource_id=lid,
                    )
                )
                await metadata_repositories(repo).bindings.replace(
                    BindingLookup(
                        model_id=run["modelId"],
                        revision_id=run["revisionId"],
                        resource_kind="measureValues",
                        resource_id=lid,
                    ),
                    {
                        **binding,
                        "inputHash": fingerprint,
                        "definitionHash": fingerprint,
                    },
                )
                await metadata_repositories(repo).artifacts.replace(
                    ArtifactLookup(artifact_id=artifact["artifactId"]),
                    {
                        **artifact,
                        "inputHash": fingerprint,
                        "materialisationManifest": manifests[lid],
                    },
                )
            await metadata_repositories(repo).activation.complete(
                run,
                current,
                generationId=snapshots[run["resourceId"]]["generationId"],
                reused=True,
            )

        await self.builds.store.transaction(activate)
