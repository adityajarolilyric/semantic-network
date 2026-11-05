from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    RevisionLookup,
    RunLookup,
)
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..engine.measure_edits import MeasureEditPlanner
from ..engine.planning_view import PlanningViewCompiler, PlanningViewPlanner
from ..engine.read_leases import ArtifactReadLeases
from .base import Service


class PlanningViewService(Service):
    """Capture/pin a coherent network, select a safe source and execute bounded reads."""

    def __init__(self, store, settings, builds):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.builds = settings, builds
        self.read_leases = ArtifactReadLeases(store)
        self.executor, self.warehouse = builds.executor, builds.executor.warehouse

    async def options(self, model, revision):
        await metadata_repositories(self.store.repository).revisions.require(
            RevisionLookup(model_id=model, revision_id=revision), "Revision"
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
        builds = await metadata_repositories(self.store.repository).runs.list(
            RunLookup(
                model_id=model,
                revision_id=revision,
                resource_kind="measureSet",
                state="succeeded",
                is_edit_in=[False, None],
            ),
            order_by="createdAt",
            newest_first=True,
            limit=1,
        )
        return {
            "buildId": builds[0]["runId"] if builds else None,
            "dimensions": dimensions,
            "planningLevels": [
                {
                    "planningLevelId": o["objectId"],
                    "name": o["name"],
                    "columns": [
                        *[
                            {"columnId": c["grainColumnId"], "name": c["name"]}
                            for c in o["definition"]["grain"]["columns"]
                        ],
                        *[
                            {"columnId": c["columnId"], "name": c["name"]}
                            for c in o["definition"].get("populationAttributes", [])
                        ],
                    ],
                }
                for o in objects
                if o["kind"] == "planningLevel"
            ],
            "measures": [
                {"measureId": o["objectId"], "name": o["name"]}
                for o in objects
                if o["kind"] == "measure"
            ],
        }

    async def current_snapshot(self, repo, model, revision):
        (
            rev,
            objects,
            populations,
            managed,
            _,
            _,
            fingerprint,
        ) = await self.builds.capture_build_inputs(repo, model, revision)
        bindings = await metadata_repositories(repo).bindings.list(
            BindingLookup(
                model_id=model, revision_id=revision, resource_kind="measureValues"
            ),
            order_by="resourceId",
            limit=self.max_objects,
        )
        values = {}
        for binding in bindings:
            if binding["inputHash"] != fingerprint:
                continue
            artifact = await metadata_repositories(repo).artifacts.find(
                ArtifactLookup(artifact_id=binding["artifactId"])
            )
            if (
                artifact
                and artifact["state"] == "ready"
                and artifact.get("populationArtifactId")
                == populations.get(binding["resourceId"], {}).get("artifactId")
            ):
                values[binding["resourceId"]] = artifact
        base_id = rev["basePlanningLevelId"]
        values = {
            lid: artifact
            for lid, artifact in values.items()
            if base_id in values
            and artifact["generationId"] == values[base_id]["generationId"]
        }
        return rev, objects, populations, managed, values, base_id, fingerprint

    async def capture(self, repo, model, revision, request, read_id):
        (
            rev,
            objects,
            populations,
            managed,
            values,
            base_id,
            _,
        ) = await self.current_snapshot(repo, model, revision)
        if base_id not in values:
            raise Conflict(
                "Build Semantic Network in Materialise before opening a Planning View. Saved definitions and planning populations must be current."
            )
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
        await self.read_leases.acquire(
            repo,
            read_id,
            inputs.values(),
            resource="Planning View input",
            retired_message="A Planning View input was retired. Retry the query.",
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
        async with self.read_leases.hold(
            read_id, artifact_ids, "Planning View read lease expired. Retry the query."
        ) as heartbeat:
            await self.builds.storage.ensure_inputs(
                [
                    artifact
                    for level in dict.fromkeys(
                        [plan.level_id, *plan.count_source_levels]
                    )
                    for artifact in (populations[level], values[level])
                ]
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

            rows = await self.warehouse.queries.planning_view(
                compiled,
                self.executor.execution_settings(),
                self.settings.planning_view_max_groups,
            )
            await self.executor.checks(compiled)
            if heartbeat.done():
                heartbeat.result()

            # Fence successful reads if metadata or the materialised generation changed
            # while executing. The pinned rows themselves remain an immutable snapshot.
            async def verify(repo):
                rev = await metadata_repositories(repo).revisions.require(
                    RevisionLookup(model_id=model, revision_id=revision), "Revision"
                )
                if rev["version"] != version:
                    raise Conflict(
                        "The semantic network changed while querying. Refresh Planning View."
                    )
                await self.read_leases.verify(
                    repo,
                    read_id,
                    artifact_ids,
                    "Planning View read lease expired. Retry the query.",
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
            width = n_rows + n_columns + len(request.measures)
            cell_states = [
                [
                    "mixed" if str(flag).lower() in {"1", "true"} else None
                    for flag in row[width:]
                ]
                for row in rows
            ]
            rows = [row[:width] for row in rows]
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
                        "valueType": by_id[mid]["definition"]["resultType"]["type"],
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
                "cellStates": cell_states,
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
