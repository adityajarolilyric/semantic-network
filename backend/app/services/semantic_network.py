"""One durable build command owns population preparation and measure materialisation."""

from uuid import uuid4

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..engine.sql.contracts import digest
from ..engine.graph.dependencies import ordered_dimensions
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import RevisionLookup, RunLookup
from .base import Service, now, scope


class SemanticNetworkService(Service):
    def __init__(self, store, settings, populations, measures, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.populations, self.measures, self.managed = populations, measures, managed
        self.gate = measures.gate

    @staticmethod
    def definition_fingerprint(revision, objects):
        return digest(
            [
                revision["baseDimensionId"],
                sorted((o["objectId"], o["version"]) for o in objects),
            ]
        )

    @staticmethod
    def ordered_dimensions(objects):
        return ordered_dimensions(objects)

    async def submit(
        self,
        model,
        revision,
        expected,
        key,
        mode="incremental",
        *,
        population_dimension_id=None,
        command_hash=None,
        query_memory_bytes=None,
    ):
        if not key or len(key) > 128 or mode not in {"full", "incremental"}:
            raise MetadataError(
                "invalid_build_request",
                "Choose a supported build mode and idempotency key.",
            )
        if query_memory_bytes is not None and (
            type(query_memory_bytes) is not int
            or not 128 * 1024**2 <= query_memory_bytes <= 4 * 1024**3
        ):
            raise MetadataError(
                "invalid_query_budget",
                "Query memory must be between 128 MiB and 4 GiB.",
            )
        request_hash = command_hash or digest(
            ["semanticNetwork", expected, mode]
            + ([population_dimension_id] if population_dimension_id else [])
            + ([query_memory_bytes] if query_memory_bytes is not None else [])
        )

        async def operation(repo):
            repositories = metadata_repositories(repo)
            previous = await repositories.runs.find(
                RunLookup(model_id=model, revision_id=revision, idempotency_key=key)
            )
            if previous:
                if previous["requestHash"] != request_hash:
                    raise Conflict("This idempotency key belongs to another request.")
                return self.managed.public_run(previous)
            rev = await repositories.revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            self.check_version(rev, expected)
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            objects = await self.graph_objects(repo, model, revision)
            self.validate(
                await self.effective(repo, model, revision, objects),
                rev["baseDimensionId"],
            )
            if not population_dimension_id and not any(
                o["kind"] == "measure" for o in objects
            ):
                raise MetadataError("no_measures", "Create measures before building.")
            active = await repositories.runs.list(
                RunLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="semanticNetwork",
                    state_in=["queued", "running", "waiting"],
                ),
                order_by="runId",
                limit=1,
            )
            if active:
                raise Conflict("A Semantic Network build is already in progress.")
            other_values = await repositories.runs.list(
                RunLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="measureSet",
                    state_in=["queued", "running", "waiting"],
                ),
                limit=1,
            )
            if other_values:
                raise Conflict(
                    "A measure build or edit is still running. Retry after it finishes."
                )
            last_network = await self.gate.latest(repo, model, revision)
            recovery_measures = []
            build_mode = mode
            if self.gate.recovery_required(last_network):
                if last_network["buildMode"] == "full":
                    build_mode = "full"
                failed_child = await repositories.runs.find(
                    RunLookup(run_id=last_network.get("childRunId"))
                )
                recovery_measures = (failed_child or {}).get("dirtyMeasures") or [
                    o["objectId"] for o in objects if o["kind"] == "measure"
                ]
            population_jobs = await repositories.runs.list(
                RunLookup(
                    model_id=model,
                    revision_id=revision,
                    resource_kind="dimensionSet",
                    state_in=["queued", "running", "waiting"],
                ),
                order_by="runId",
                limit=1,
            )
            if population_jobs:
                raise Conflict(
                    "A Dimension population build is already in progress. Retry after it finishes."
                )
            ordered = self.ordered_dimensions(objects)
            if population_dimension_id:
                by_id = {obj["objectId"]: obj for obj in ordered}
                required, parent = set(), population_dimension_id
                while parent:
                    if parent not in by_id:
                        raise MetadataError(
                            "missing_dimension",
                            "The Dimension to build no longer exists.",
                        )
                    required.add(parent)
                    population = by_id[parent]["definition"]["population"]
                    parent = (
                        population.get("sourceDimensionId")
                        if population["operation"] == "distinct_projection"
                        else None
                    )
                ordered = [obj for obj in ordered if obj["objectId"] in required]
            # A shared revision write serialises competing submissions across API processes.
            await self.touch_revision(repo, model, revision)
            timestamp = now()
            run = {
                **scope(model, revision),
                "runId": "run_" + uuid4().hex,
                "resourceKind": "dimensionSet"
                if population_dimension_id
                else "semanticNetwork",
                "resourceId": population_dimension_id or revision,
                "populationOnly": bool(population_dimension_id),
                "state": "queued",
                "requestHash": request_hash,
                "idempotencyKey": key,
                "createdAt": timestamp,
                "updatedAt": timestamp,
                "fence": 0,
                "owner": None,
                "leaseUntil": timestamp,
                "error": None,
                "buildMode": build_mode,
                "queryMemoryBytes": query_memory_bytes
                or self.measures.settings.planning_max_bytes,
                "recoveryMeasures": recovery_measures,
                "definitionFingerprint": self.definition_fingerprint(rev, objects),
                "dimensionIds": [o["objectId"] for o in ordered],
                "dimensionIndex": 0,
                "childRunId": None,
            }
            await repositories.runs.insert(run)
            return self.managed.public_run(run)

        return await self.store.transaction(operation)
