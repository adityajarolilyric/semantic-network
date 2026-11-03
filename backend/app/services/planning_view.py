import asyncio
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..engine.planning_view import PlanningViewPlanner, PlanningViewCompiler
from ..engine.measure_edits import MeasureEditPlanner
from .base import Service, now, scope


class PlanningViewService(Service):
    """Capture/pin a coherent network, select a safe source and execute bounded reads."""

    def __init__(self, store, settings, builds):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.builds = settings, builds
        self.executor, self.warehouse = builds.executor, builds.executor.warehouse

    async def options(self, model, revision):
        await self.require(
            self.store.repository,
            "semantic_revisions",
            scope(model, revision),
            "Revision",
        )
        objects = await self.objects(self.store.repository, model, revision)
        dimensions = []
        for obj in objects:
            if obj["kind"] != "dimension":
                continue
            exposed = set(obj["definition"]["keyColumnIds"]) | set(
                obj["definition"]["attributeColumnIds"]
            )
            dimensions.append(
                {
                    "dimensionId": obj["objectId"],
                    "name": obj["name"],
                    "isTimeDim": obj["isTimeDim"],
                    "columns": [
                        {"columnId": c["columnId"], "name": c["displayName"]}
                        for c in obj["definition"]["columns"]
                        if c["columnId"] in exposed
                    ],
                }
            )
        builds = await self.store.repository.list(
            "engine_runs",
            {
                **scope(model, revision),
                "resourceKind": "measureSet",
                "state": "succeeded",
                "isEdit": {"$in": [False, None]},
            },
            key="createdAt",
            descending=True,
            limit=1,
        )
        return {
            "buildId": builds[0]["runId"] if builds else None,
            "dimensions": dimensions,
            "measures": [
                {"measureId": o["objectId"], "name": o["name"]}
                for o in objects
                if o["kind"] == "measure"
            ],
        }

    async def capture(self, repo, model, revision, request, read_id):
        (
            rev,
            objects,
            populations,
            managed,
            _,
            _,
            fingerprint,
        ) = await self.builds.capture(repo, model, revision)
        bindings = await repo.list(
            "engine_artifact_bindings",
            {**scope(model, revision), "resourceKind": "measureValues"},
            key="resourceId",
            limit=self.max_objects,
        )
        values = {}
        for binding in bindings:
            if binding["inputHash"] != fingerprint:
                continue
            artifact = await repo.get(
                "engine_artifacts", {"artifactId": binding["artifactId"]}
            )
            if (
                artifact
                and artifact["state"] == "ready"
                and artifact.get("populationArtifactId")
                == populations.get(binding["resourceId"], {}).get("artifactId")
            ):
                values[binding["resourceId"]] = artifact
        base_id = rev["basePlanningLevelId"]
        if base_id not in values:
            raise Conflict(
                "Build Semantic Network in Materialise before opening a Planning View. Saved definitions and planning populations must be current."
            )
        values = {
            lid: artifact
            for lid, artifact in values.items()
            if artifact["generationId"] == values[base_id]["generationId"]
        }
        planner = PlanningViewPlanner(objects, base_id)
        plan = planner.plan(request, populations, values)
        inputs = {
            a["artifactId"]: a
            for a in [
                *populations.values(),
                *values.values(),
                *managed.values(),
            ]
        }
        for artifact in inputs.values():
            current = await self.require(
                repo,
                "engine_artifacts",
                {"artifactId": artifact["artifactId"]},
                "Planning View input",
            )
            if current["state"] != "ready":
                raise Conflict("A Planning View input was retired. Retry the query.")
            await repo.replace(
                "engine_artifacts",
                {"artifactId": current["artifactId"]},
                {**current, "pinVersion": current.get("pinVersion", 0) + 1},
            )
            await repo.insert(
                "engine_read_leases",
                {
                    "readId": read_id,
                    "artifactId": artifact["artifactId"],
                    "expiresAt": now() + timedelta(seconds=180),
                },
            )
        return (
            objects,
            populations,
            managed,
            values,
            base_id,
            plan,
            list(inputs),
            rev["version"],
            rev["approvalStatus"] == "draft",
        )

    async def renew(self, read_id, artifact_ids):
        while True:
            await asyncio.sleep(15)

            async def operation(repo):
                for aid in artifact_ids:
                    identity = {"readId": read_id, "artifactId": aid}
                    lease = await repo.get("engine_read_leases", identity)
                    if not lease or lease["expiresAt"] <= now():
                        raise Conflict(
                            "Planning View read lease expired. Retry the query."
                        )
                    await repo.replace(
                        "engine_read_leases",
                        identity,
                        {**lease, "expiresAt": now() + timedelta(seconds=180)},
                    )

            await self.store.transaction(operation)

    async def query(self, model, revision, request):
        read_id = "read_" + uuid4().hex
        captured = await self.store.transaction(
            lambda repo: self.capture(repo, model, revision, request, read_id)
        )
        (
            objects,
            populations,
            managed,
            values,
            base_id,
            plan,
            artifact_ids,
            version,
            draft,
        ) = captured
        heartbeat = asyncio.create_task(self.renew(read_id, artifact_ids))
        try:
            await self.builds.storage.ensure_inputs(
                [populations[plan.level_id], values[plan.level_id]]
            )
            compiler = PlanningViewCompiler(
                self.settings.clickhouse_database,
                objects,
                populations,
                managed,
                base_id,
                values,
                plan,
            )
            compiler.max_nodes = self.settings.measure_max_nodes
            compiled = compiler.compile(request.measures)
            if len(compiled.sql.encode()) > self.settings.measure_max_sql_bytes:
                raise MetadataError(
                    "planning_view_sql_limit",
                    "The selected formulas exceed the query complexity limit. Choose fewer measures.",
                )
            await self.executor.checks(compiled)

            def execute(connect):
                result = connect().query(
                    f"SELECT * FROM ({compiled.sql}) ORDER BY ALL LIMIT {self.settings.planning_view_max_groups + 1}",
                    settings=self.executor.execution_settings(),
                )
                if len(result.result_rows) > self.settings.planning_view_max_groups:
                    raise MetadataError(
                        "planning_view_limit",
                        "This pivot has too many groups. Choose fewer attributes or a coarser grain.",
                    )
                return [
                    [self.warehouse.cell(v) for v in row] for row in result.result_rows
                ]

            rows = await self.warehouse.run(execute)
            await self.executor.checks(compiled)
            if heartbeat.done():
                heartbeat.result()

            # Fence successful reads if metadata or the materialised generation changed
            # while executing. The pinned rows themselves remain an immutable snapshot.
            async def verify(repo):
                rev = await self.require(
                    repo, "semantic_revisions", scope(model, revision), "Revision"
                )
                if rev["version"] != version:
                    raise Conflict(
                        "The semantic network changed while querying. Refresh Planning View."
                    )
                for aid in artifact_ids:
                    lease = await repo.get(
                        "engine_read_leases", {"readId": read_id, "artifactId": aid}
                    )
                    if not lease or lease["expiresAt"] <= now():
                        raise Conflict(
                            "Planning View read lease expired. Retry the query."
                        )

            await self.store.transaction(verify)
            n_rows, n_columns = len(request.rows), len(request.columns)
            row_keys = {tuple(row[:n_rows]) for row in rows}
            column_keys = {tuple(row[n_rows : n_rows + n_columns]) for row in rows}
            cells = len(row_keys) * len(column_keys) * len(request.measures)
            if cells > self.settings.planning_view_max_cells:
                raise MetadataError(
                    "planning_view_limit",
                    "This pivot would be too wide or too large to display. Move an attribute from columns to rows, or select fewer fields.",
                )
            by_id = {o["objectId"]: o for o in objects}
            return {
                "fields": [
                    {k: v for k, v in field.items() if k != "coordinate"}
                    for field in plan.fields
                ],
                "measures": [
                    {
                        "measureId": mid,
                        "name": by_id[mid]["name"],
                        **MeasureEditPlanner(objects, base_id, plan.fields).capability(
                            mid, values, draft
                        ),
                        "calculation": "reused"
                        if mid in plan.leaves
                        else "recalculated",
                    }
                    for mid in request.measures
                ],
                "warnings": [
                    {
                        "measureId": mid,
                        "message": by_id[mid]["name"]
                        + ": some finer cells are unavailable because allocation weights are missing or total zero. Incomplete aggregates are unavailable unless a native representation can supply them.",
                    }
                    for mid in plan.leaves
                    if values[plan.level_id].get("incompleteMeasures", {}).get(mid)
                ],
                "rows": rows,
                "plan": {
                    "planningLevelId": plan.level_id,
                    "planningLevelName": by_id[plan.level_id]["name"],
                    "usesBase": plan.level_id == base_id,
                    "exactGrain": plan.exact,
                    "nativeOnly": plan.native_only,
                    "explanation": plan.explanation,
                    "generationId": values[plan.level_id]["generationId"],
                },
                "editContext": {
                    "revisionVersion": version,
                    "generationId": values[base_id]["generationId"],
                },
                "groupCount": len(rows),
                "cellCount": cells,
            }
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat

            async def release(repo):
                for aid in artifact_ids:
                    await repo.delete(
                        "engine_read_leases", {"readId": read_id, "artifactId": aid}
                    )

            # Middleware's workspace lease keeps maintenance from draining until
            # this finally block has completed.
            await self.store.transaction(release)
