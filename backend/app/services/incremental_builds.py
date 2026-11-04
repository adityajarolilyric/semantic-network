"""Capture and atomically reuse a coherent values generation without source reload."""

from ..core.errors import Conflict, MetadataError
from ..engine.incremental import MaterialisationManifest, INCREMENTAL_POLICY, reusable
from ..engine.measure_compiler import MEASURE_COMPILER_VERSION
from ..services.measure_types import MEASURE_TYPE_POLICY
from .base import now, scope


class IncrementalBuildCoordinator:
    def __init__(self, builds):
        self.builds = builds

    @staticmethod
    def validate_runtime_policy(run):
        if run.get("buildMode") == "incremental" and (
            run.get("incrementalPolicy") != INCREMENTAL_POLICY
            or run.get("incrementalCompilerVersion") != MEASURE_COMPILER_VERSION
        ):
            raise Conflict(
                "The incremental engine changed after this job was submitted. Resubmit the build; existing values were preserved."
            )

    async def baseline(self, repo, artifact):
        if (
            artifact.get("numericPolicy") != MEASURE_TYPE_POLICY
            or artifact.get("compilerVersion") != MEASURE_COMPILER_VERSION
        ):
            raise Conflict(
                "A Full Refresh is required to establish a compatible incremental baseline. Existing values were preserved."
            )
        manifest = artifact.get("materialisationManifest")
        if manifest and manifest.get("policy") == INCREMENTAL_POLICY:
            return manifest
        # Reconstruct a pre-upgrade baseline only from its immutable captured inputs.
        run = await repo.get("engine_runs", {"runId": artifact["runId"]})
        docs = await repo.list(
            "engine_run_inputs",
            {"runId": artifact["runId"]},
            key="inputId",
            limit=self.builds.max_objects * 4 + 3,
        )
        objects = [d["document"] for d in docs if not d["inputId"].startswith("__")]
        populations = {
            d["inputId"][13:]: d["document"]
            for d in docs
            if d["inputId"].startswith("__population_")
        }
        managed = {
            d["inputId"][10:]: d["document"]["artifact"]
            for d in docs
            if d["inputId"].startswith("__managed_")
        }
        if not run or not objects or artifact["levelId"] not in populations:
            raise Conflict(
                "Captured inputs for incremental reuse are unavailable. Use Full Refresh; no existing values were changed."
            )
        try:
            manifests = MaterialisationManifest(
                objects, populations, managed, run["resourceId"]
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
            for lid in populations:
                binding = await repo.get(
                    "engine_artifact_bindings",
                    {
                        **scope(model, revision),
                        "resourceKind": "measureValues",
                        "resourceId": lid,
                    },
                )
                if not binding:
                    continue
                artifact = await self.builds.require(
                    repo,
                    "engine_artifacts",
                    {"artifactId": binding["artifactId"]},
                    "Current values",
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
        active = await repo.list(
            "engine_artifact_bindings",
            {**scope(model, revision), "resourceKind": "measureValues"},
            key="resourceId",
            limit=self.builds.max_objects + 1,
        )
        reuse_only = (
            {binding["resourceId"] for binding in active} == set(populations)
            and bool(snapshots)
            and set(snapshots) == set(populations)
            and all(
                snapshots[lid]["materialisationManifest"] == manifests[lid]
                and snapshots[lid]["populationArtifactId"]
                == populations[lid]["artifactId"]
                for lid in populations
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
            binding = await repo.get(
                "engine_artifact_bindings",
                {
                    **scope(run["modelId"], run["revisionId"]),
                    "resourceKind": "measureValues",
                    "resourceId": lid,
                },
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
            rev, *_, fingerprint = await self.builds.capture(
                repo, run["modelId"], run["revisionId"]
            )
            self.builds.check_version(rev, run["revisionVersion"])
            if rev["approvalStatus"] != "draft":
                raise Conflict("Published revisions cannot be rebuilt.")
            if fingerprint != run["inputHash"]:
                raise Conflict("Inputs changed during incremental reuse.")
            await self.check_snapshots(repo, run, snapshots)
            for lid, artifact in snapshots.items():
                identity = {
                    **scope(run["modelId"], run["revisionId"]),
                    "resourceKind": "measureValues",
                    "resourceId": lid,
                }
                binding = await repo.get("engine_artifact_bindings", identity)
                await repo.replace(
                    "engine_artifact_bindings",
                    identity,
                    {
                        **binding,
                        "inputHash": fingerprint,
                        "definitionHash": fingerprint,
                    },
                )
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": artifact["artifactId"]},
                    {
                        **artifact,
                        "inputHash": fingerprint,
                        "materialisationManifest": manifests[lid],
                    },
                )
            await repo.replace(
                "engine_runs",
                {"runId": run["runId"], "fence": run["fence"]},
                {
                    **current,
                    "state": "succeeded",
                    "updatedAt": now(),
                    "generationId": snapshots[run["resourceId"]]["generationId"],
                    "reused": True,
                },
            )

        await self.builds.store.transaction(activate)
