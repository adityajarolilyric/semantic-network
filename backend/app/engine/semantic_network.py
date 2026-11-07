"""A resumable coordinator: children execute on the existing fenced worker queue."""

from datetime import timedelta

from ..core.errors import Conflict, MetadataError
from ..core.metadata import utc_now as now
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import RevisionLookup, RunLookup
from .virtual_populations import is_virtual_dimension
from .source_view_preparation import SourceViewPreparation


class SemanticNetworkHandler:
    def __init__(self, service):
        self.service = service
        self.source_views = SourceViewPreparation(service.managed)

    async def execute_run(self, run, worker):
        service = self.service

        async def capture(repo):
            current = await worker.fenced(repo, run)
            revision = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=run["modelId"], revision_id=run["revisionId"]),
                "Revision",
            )
            objects = await service.graph_objects(
                repo, run["modelId"], run["revisionId"]
            )
            if (
                service.definition_fingerprint(revision, objects)
                != current["definitionFingerprint"]
            ):
                raise Conflict(
                    "Definitions changed during the Semantic Network build. Build again using the saved definitions."
                )
            child = (
                await metadata_repositories(repo).runs.find(
                    RunLookup(run_id=current["childRunId"])
                )
                if current.get("childRunId")
                else None
            )
            return current, revision, objects, child

        current, revision, objects, child = await service.store.transaction(capture)
        if not current.get("readersDrained") and not run.get("populationOnly"):
            await worker.step(run, "read_barrier", "running")
            await service.gate.drain()
            await worker.step(run, "read_barrier", "succeeded")

            async def drained(repo):
                active = await worker.fenced(repo, run)
                updated = {**active, "readersDrained": True}
                await metadata_repositories(repo).runs.replace(
                    RunLookup(run_id=run["runId"]), updated
                )
                return updated

            current = await service.store.transaction(drained)
        if child and child["state"] in {"queued", "running", "waiting"}:
            return await self.defer(run, worker)
        if child and child["state"] != "succeeded":
            error = child.get("error") or {}
            if child["state"] == "superseded":
                raise Conflict(error.get("message", "A build stage was superseded."))
            raise MetadataError(
                error.get("code", "semantic_build_failed"),
                error.get("message", "A build stage failed."),
                details=error.get("details"),
            )
        if (
            run.get("buildMode") == "full"
            and not run.get("populationOnly")
            and not current.get("sourceViewsPrepared")
        ):
            await worker.step(run, "source_views", "running")
            stage = await self.source_views.next_build(run, objects)
            if stage:
                return await self.defer(run, worker, childRunId=stage["runId"])

            async def prepared(repo):
                active = await worker.fenced(repo, run)
                updated = {
                    **active,
                    "sourceViewsPrepared": True,
                    "childRunId": None,
                    "dimensionIndex": 0,
                }
                await metadata_repositories(repo).runs.replace(
                    RunLookup(run_id=run["runId"]), updated
                )
                return updated

            current = await service.store.transaction(prepared)
            child = None
            await worker.step(run, "source_views", "succeeded")
        index = current["dimensionIndex"]
        if child and child["resourceKind"] == "measureSet":

            async def complete(repo):
                active = await worker.fenced(repo, run)
                await metadata_repositories(repo).activation.complete(
                    run,
                    active,
                    reused=child.get("reused", False),
                    measureRunId=child["runId"],
                    generationId=child.get("generationId"),
                )

            return await service.store.transaction(complete)
        if child and child["resourceKind"] in {"dimension", "dimensionPreview"}:
            index += 1
        dimensions = {o["objectId"]: o for o in objects if o["kind"] == "dimension"}
        while index < len(current["dimensionIds"]):
            dimension = dimensions[current["dimensionIds"][index]]
            if is_virtual_dimension(dimension):
                index += 1
                continue
            try:
                status = await service.populations.status(
                    run["modelId"], run["revisionId"], dimension["objectId"]
                )
            except MetadataError:
                status = {"state": "not_built"}
            if status["state"] == "ready":
                index += 1
                continue
            stage = await service.populations.submit(
                run["modelId"],
                run["revisionId"],
                dimension["objectId"],
                dimension["version"],
                run["runId"] + "_" + str(index),
                query_memory_bytes=run.get("queryMemoryBytes"),
            )
            return await self.defer(
                run, worker, dimensionIndex=index, childRunId=stage["runId"]
            )
        if current.get("populationOnly"):

            async def complete_populations(repo):
                active = await worker.fenced(repo, run)
                await metadata_repositories(repo).activation.complete(run, active)

            return await service.store.transaction(complete_populations)
        # Population activation updates revision guards; definitions were checked above.
        revision = await metadata_repositories(
            service.store.repository
        ).revisions.require(
            RevisionLookup(model_id=run["modelId"], revision_id=run["revisionId"]),
            "Revision",
        )
        previous = await metadata_repositories(service.store.repository).runs.find(
            RunLookup(
                model_id=run["modelId"],
                revision_id=run["revisionId"],
                idempotency_key=run["runId"] + "_values",
            )
        )
        stage = (
            service.managed.public_run(previous)
            if previous
            else await service.measures.submit(
                run["modelId"],
                run["revisionId"],
                revision["version"],
                run["runId"] + "_values",
                mode=run["buildMode"],
                network_run_id=run["runId"],
                forced_measures=run.get("recoveryMeasures", []),
                query_memory_bytes=run.get("queryMemoryBytes"),
            )
        )
        return await self.defer(
            run, worker, dimensionIndex=index, childRunId=stage["runId"]
        )

    async def defer(self, run, worker, **progress):
        async def operation(repo):
            current = await worker.fenced(repo, run)
            await metadata_repositories(repo).runs.replace(
                RunLookup(run_id=run["runId"], fence=run["fence"]),
                {
                    **current,
                    **progress,
                    "state": "waiting",
                    "leaseUntil": now() + timedelta(seconds=1),
                    "updatedAt": now(),
                },
            )

        await self.service.store.transaction(operation)
