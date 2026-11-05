"""Planning candidate execution and guarded activation; HTTP services capture intent."""

from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import ArtifactLookup, RevisionLookup

from dataclasses import dataclass

from ..core.errors import Conflict, ImmutableRevision
from ..core.metadata import revision_scope as scope
from ..core.metadata import utc_now as now
from ..engine.captured_inputs import CapturedRunInputs
from ..engine.contracts import digest
from ..engine.table_storage import STORAGE_POLICY


@dataclass(frozen=True)
class PlanningBuildCandidate:
    level: dict
    captured_inputs: dict
    artifact: dict
    outcome: dict
    compiler_version: str


class PlanningBuildHandler:
    def __init__(self, builds):
        self.builds = builds
        self.activation = PlanningBuildActivation(builds)

    async def execute_run(self, run, worker):
        builds = self.builds
        if run.get("storagePolicy") != STORAGE_POLICY:
            raise Conflict(
                "This build predates the table-storage upgrade. Submit a new build."
            )
        captured_inputs = await CapturedRunInputs.load(
            builds.store.repository, run["runId"], builds.max_objects * 2 + 1
        )
        objects = captured_inputs.objects
        level = next(
            document
            for document in objects
            if document["objectId"] == run["resourceId"]
        )
        captured = {
            **run["capture"],
            "relations": captured_inputs.relations,
            "managed": captured_inputs.relation_definitions,
        }
        await builds.managed.input_verifier.verify(
            objects,
            {
                source_id: captured["relations"][source_id]
                for source_id in captured["managed"]
            },
            captured["managed"],
        )
        await builds.storage.ensure_inputs(captured["relations"].values())
        plan = builds.planner.plan(level, objects, captured["relations"])
        compiled = builds.compiler.compile(plan)
        estimate = await builds.executor.estimate(plan, builds.compiler)
        await worker.step(
            run,
            "compile",
            "succeeded",
            sql=compiled.sql,
            planHash=digest(compiled.sql),
            estimate=estimate,
            compilerVersion=compiled.compiler_version,
        )
        await worker.step(run, "checks", "running")
        await builds.executor.checks(compiled)
        await worker.step(run, "checks", "succeeded")
        artifact = await metadata_repositories(
            builds.store.repository
        ).artifacts.require(
            ArtifactLookup(artifact_id=run["artifactId"]), "Candidate table"
        )
        await worker.step(run, "materialize", "running")
        outcome = await builds.executor.build(artifact, compiled, plan)
        await builds.executor.checks(compiled)
        await worker.step(run, "materialize", "succeeded", rowCount=outcome["rowCount"])

        candidate = PlanningBuildCandidate(
            level, captured, artifact, outcome, compiled.compiler_version
        )
        await self.activation.activate(run, worker, candidate)


class PlanningBuildActivation:
    def __init__(self, builds):
        self.builds = builds

    async def activate(self, run, worker, candidate: PlanningBuildCandidate):
        builds = self.builds
        level, captured, artifact, outcome = (
            candidate.level,
            candidate.captured_inputs,
            candidate.artifact,
            candidate.outcome,
        )

        async def activate(repo):
            current_run = await worker.fenced(repo, run)
            query = scope(run["modelId"], run["revisionId"])
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=run["modelId"], revision_id=run["revisionId"]),
                "Revision",
            )
            builds.check_version(revision, run["revisionVersion"])
            if revision["approvalStatus"] != "draft":
                raise ImmutableRevision()
            current_objects = await builds.objects(
                repo, run["modelId"], run["revisionId"]
            )
            current = next(
                (o for o in current_objects if o["objectId"] == run["resourceId"]), None
            )
            if run.get("previewOnly"):
                if (current["version"] if current else None) != run["expectedVersion"]:
                    raise Conflict(
                        "The saved planning definition changed during preview."
                    )
                proposed = level
                proposed_objects = [
                    o for o in current_objects if o["objectId"] != proposed["objectId"]
                ] + [proposed]
                inputs = await builds.capture_population_inputs(
                    repo, run["modelId"], run["revisionId"], proposed, proposed_objects
                )
            else:
                if not current or current["version"] != run["expectedVersion"]:
                    raise Conflict()
                inputs = await builds.capture_population_inputs(
                    repo, run["modelId"], run["revisionId"], current, current_objects
                )
            if inputs["inputHash"] != run["inputHash"]:
                raise Conflict("Build dependencies changed during execution.")
            identity = {
                **query,
                "resourceKind": "planningLevel",
                "resourceId": run["resourceId"],
            }
            binding = {
                **identity,
                "artifactId": artifact["artifactId"],
                "definitionHash": run["definitionHash"],
                "inputHash": run["inputHash"],
                "builtAt": now(),
            }
            if not run.get("previewOnly"):
                await metadata_repositories(repo).activation.bind(identity, binding)
            await metadata_repositories(repo).artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {
                    **artifact,
                    **outcome,
                    "state": "ready",
                    "compilerVersion": candidate.compiler_version,
                    "dependencies": captured["bindings"],
                    "builtAt": now(),
                },
            )
            # Publication and competing materializations observe a common revision guard.
            if not run.get("previewOnly"):
                await builds.touch_revision(repo, run["modelId"], run["revisionId"])
            await metadata_repositories(repo).activation.complete(
                run, current_run, rowCount=outcome["rowCount"]
            )

        await builds.store.transaction(activate)
