from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ArtifactLookup,
    BindingLookup,
    ObjectLookup,
    RevisionLookup,
)
from ..engine.preview_plans import compile_resolution_preview
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..engine.compilation_context import CompilationContext
from ..engine.date_range_resolution import calendar_source_ids
from ..engine.input_pipeline import InputPipelineCompiler, PreviewTarget
from ..engine.measure_compiler import MeasureCompiler
from ..engine.read_leases import ArtifactReadLeases
from ..engine.relationships import Coordinate, LevelRelationshipAnalyzer
from ..engine.transaction_resolution import (
    TransactionResolutionPlanner,
    prepare_resolution_profiles,
)
from .base import Service, scope


class TransactionResolutionService(Service):
    def __init__(self, store, settings, planning, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.planning, self.managed = settings, planning, managed
        self.read_leases = ArtifactReadLeases(store)
        self.executor, self.warehouse = planning.executor, planning.executor.warehouse

    async def options(self, model, revision, level_id=None):
        repo = self.store.repository
        objects = await self.objects(repo, model, revision)
        level = (
            await metadata_repositories(repo).objects.require(
                ObjectLookup(
                    model_id=model,
                    revision_id=revision,
                    object_id=level_id,
                    kind="planningLevel",
                ),
                "Native planning level",
            )
            if level_id
            else {"definition": {"grain": {"keyIds": [], "columns": []}}}
        )
        rel = LevelRelationshipAnalyzer(objects)
        binding = await metadata_repositories(repo).bindings.find(
            BindingLookup(
                model_id=model,
                revision_id=revision,
                resource_kind="planningLevel",
                resource_id=level_id,
            )
        )
        artifact = (
            await metadata_repositories(repo).artifacts.find(
                ArtifactLookup(artifact_id=binding["artifactId"])
            )
            if binding
            else None
        )
        names = {c["columnId"]: c["name"] for c in (artifact or {}).get("columns", [])}
        names.update(
            {
                c["grainColumnId"]: c["name"]
                for c in level["definition"]["grain"]["columns"]
            }
        )
        names.update(
            {
                c["columnId"]: c["name"]
                for c in level["definition"].get("populationAttributes", [])
            }
        )
        targets = [
            {
                "target": {"dimensionId": None, "columnId": cid},
                "label": "Native · " + names.get(cid, cid),
            }
            for cid in (rel.outputs(level_id) if level_id else {})
            if cid in names
        ]
        for dim in rel.dimensions.values():
            exposed = set(dim["definition"]["keyColumnIds"]) | set(
                dim["definition"]["attributeColumnIds"]
            )
            for col in dim["definition"]["columns"]:
                coordinate = Coordinate(
                    frozenset(
                        {
                            ("dimension", dim["objectId"], col["columnId"]),
                            (
                                "source",
                                dim["definition"]["sourceId"],
                                col["sourceColumnId"],
                            ),
                        }
                    )
                )
                if (
                    level_id
                    and col["columnId"] in exposed
                    and rel.resolve(level_id, coordinate)
                ):
                    targets.append(
                        {
                            "target": {
                                "dimensionId": dim["objectId"],
                                "columnId": col["columnId"],
                            },
                            "label": dim["name"] + " · " + col["displayName"],
                        }
                    )
        calendars = []
        outputs = rel.outputs(level_id) if level_id else {}
        for dim in rel.dimensions.values():
            d = dim["definition"]
            if not dim["isTimeDim"] or d["time"]["baseGrain"] != "day":
                continue
            allowed = (
                set(d["keyColumnIds"])
                | set(d["attributeColumnIds"])
                | {d["time"]["dateColumnId"]}
            )
            columns = [c for c in d["columns"] if c["columnId"] in allowed]
            suggestions = {}
            for key in level["definition"]["grain"]["keyIds"]:
                for col in columns:
                    if outputs[key].tokens & {
                        ("dimension", dim["objectId"], col["columnId"]),
                        ("source", d["sourceId"], col["sourceColumnId"]),
                    }:
                        suggestions[key] = col["columnId"]
            calendars.append(
                {
                    "dimensionId": dim["objectId"],
                    "name": dim["name"],
                    "dateColumnId": d["time"]["dateColumnId"],
                    "dateColumnName": next(
                        c["displayName"]
                        for c in columns
                        if c["columnId"] == d["time"]["dateColumnId"]
                    ),
                    "columns": [
                        {"columnId": c["columnId"], "label": c["displayName"]}
                        for c in columns
                    ],
                    "suggestions": suggestions,
                }
            )
        return {
            "targets": targets,
            "calendars": calendars,
            "timeGrainIds": [
                k for k in level["definition"]["grain"]["keyIds"] if outputs[k].is_time
            ],
        }

    async def schema(self, model, revision, transaction_id=None, payload=None):
        repo = self.store.repository
        objects = await self.effective(
            repo, model, revision, await self.objects(repo, model, revision)
        )
        tx = next(
            (
                o
                for o in objects
                if o["objectId"] == transaction_id and o["kind"] == "transaction"
            ),
            None,
        )
        if payload is not None:
            tx = payload.model_dump()
            objects = [o for o in objects if o["objectId"] != tx["objectId"]] + [tx]
        if not tx:
            raise MetadataError("not_found", "Transaction was not found.", 404)
        source = next(
            (
                o
                for o in objects
                if o["objectId"] == tx["definition"]["sourceId"]
                and o["kind"] == "source"
                and o["definition"]["inScope"]
            ),
            None,
        )
        if not source:
            raise MetadataError(
                "invalid_resolution_source", "Choose an available managed table."
            )
        if tx["definition"].get("mappingMode") != "resolve_multi_grain":
            from ..engine.calendar_resolution import CalendarResolutionPlanner

            return {
                "columns": CalendarResolutionPlanner(
                    objects, tx, source["definition"]["columns"]
                ).schema()
            }
        planner = TransactionResolutionPlanner(
            objects, tx, source["definition"]["columns"]
        )
        from ..validation.graph import SemanticGraphValidator

        types = SemanticGraphValidator(objects)
        level = types.objects[tx["definition"]["planningLevelId"]]
        population = [
            {"columnId": cid, "physicalType": types.grain_type(level, cid)}
            for cid in level["definition"]["grain"]["keyIds"]
        ]
        return {
            "columns": planner.schema(population),
            "valueColumnIds": [
                v["sourceColumnId"] for v in tx["definition"]["resolution"]["values"]
            ],
        }

    async def calendar_schema(self, model, revision, payload):
        from ..engine.calendar_resolution import CalendarResolutionPlanner

        repo = self.store.repository
        objects = await self.effective(
            repo, model, revision, await self.objects(repo, model, revision)
        )
        source = next(
            (
                o
                for o in objects
                if o["objectId"] == payload.sourceId
                and o["kind"] == "source"
                and o["definition"]["inScope"]
            ),
            None,
        )
        if not source:
            raise MetadataError("invalid_resolution_source", "Choose a managed table.")
        tx = {
            "objectId": payload.transactionId,
            "definition": {
                "sourceId": payload.sourceId,
                "calendarResolution": payload.calendarResolution.model_dump(),
                "pivot": payload.pivot.model_dump() if payload.pivot else None,
            },
        }
        return {
            "columns": CalendarResolutionPlanner(
                objects, tx, source["definition"]["columns"]
            ).schema()
        }

    async def input_schema(self, model, revision, payload):
        from ..engine.object_inputs import input_columns

        repo = self.store.repository
        objects = await self.effective(
            repo, model, revision, await self.objects(repo, model, revision)
        )
        source = next(
            (
                o
                for o in objects
                if o["objectId"] == payload.sourceId
                and o["kind"] == "source"
                and o["definition"]["inScope"]
            ),
            None,
        )
        if not source:
            raise MetadataError("invalid_resolution_source", "Choose a managed table.")
        owner = {
            "objectId": payload.objectId,
            "definition": {
                "sourceId": payload.sourceId,
                "pivot": payload.pivot.model_dump() if payload.pivot else None,
            },
        }
        return {"columns": input_columns(owner, source["definition"]["columns"])}

    async def preview_input(self, model, revision, payload):
        return await self._preview(model, revision, payload, PreviewTarget.PIVOT_INPUT)

    async def preview_calendar(self, model, revision, payload):
        return await self._preview(
            model, revision, payload, PreviewTarget.CALENDAR_INPUT
        )

    async def preview_unmapped(self, model, revision, payload):
        return await self._preview(
            model, revision, payload, PreviewTarget.UNMAPPED_TRANSACTION
        )

    async def preview_dimension(self, model, revision, payload):
        return await self._preview(
            model, revision, payload, PreviewTarget.DIMENSION_OUTPUT
        )

    async def preview(self, model, revision, payload):
        return await self._preview(
            model, revision, payload, PreviewTarget.TRANSACTION_OUTPUT
        )

    async def _preview(self, model, revision, payload, target):
        standalone = target != PreviewTarget.TRANSACTION_OUTPUT
        read_id = "read_" + uuid4().hex

        async def snapshot(repo):
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            objects = await self.objects(repo, model, revision)
            candidate = {**payload.model_dump(), **scope(model, revision), "version": 0}
            if target == PreviewTarget.CALENDAR_INPUT:
                candidate = {
                    "objectId": payload.transactionId,
                    "kind": "transaction",
                    "definition": {
                        "sourceId": payload.sourceId,
                        "calendarResolution": payload.calendarResolution.model_dump(),
                        "pivot": payload.pivot.model_dump() if payload.pivot else None,
                    },
                    **scope(model, revision),
                    "version": 0,
                }
            if target == PreviewTarget.PIVOT_INPUT:
                candidate = {
                    "objectId": payload.objectId,
                    "kind": "transaction",
                    "definition": {
                        "sourceId": payload.sourceId,
                        "pivot": payload.pivot.model_dump() if payload.pivot else None,
                    },
                    **scope(model, revision),
                    "version": 0,
                }
            if target == PreviewTarget.UNMAPPED_TRANSACTION:
                candidate = {
                    "objectId": payload.objectId,
                    "kind": "transaction",
                    "definition": {
                        "sourceId": payload.sourceId,
                        "pivot": payload.pivot.model_dump() if payload.pivot else None,
                        "calendarResolution": payload.calendarResolution.model_dump()
                        if payload.calendarResolution
                        else None,
                        "filters": payload.filters.model_dump()
                        if payload.filters
                        else None,
                    },
                    **scope(model, revision),
                    "version": 0,
                }
            objects = [o for o in objects if o["objectId"] != candidate["objectId"]] + [
                candidate
            ]
            if standalone:
                from ..engine.calendar_resolution import CalendarResolutionPlanner

                effective = await self.effective(repo, model, revision, objects)
                source = next(
                    (
                        o
                        for o in effective
                        if o["objectId"] == candidate["definition"]["sourceId"]
                        and o["kind"] == "source"
                        and o["definition"]["inScope"]
                    ),
                    None,
                )
                if not source:
                    raise MetadataError(
                        "invalid_resolution_source", "Choose a managed table."
                    )
                CalendarResolutionPlanner(
                    effective, candidate, source["definition"]["columns"]
                )
                captured, population, level = (
                    {"relations": {}, "managed": {}},
                    None,
                    None,
                )
            else:
                self.validate(
                    await self.effective(repo, model, revision, objects),
                    rev["basePlanningLevelId"],
                )
                by_id = {o["objectId"]: o for o in objects}
                level = by_id[candidate["definition"]["planningLevelId"]]
                captured = await self.planning.capture_population_inputs(
                    repo, model, revision, level, objects
                )
                binding = await metadata_repositories(repo).bindings.require(
                    BindingLookup(
                        model_id=model,
                        revision_id=revision,
                        resource_kind="planningLevel",
                        resource_id=level["objectId"],
                    ),
                    "Built native planning table",
                )
                if (
                    binding["definitionHash"] != self.planning.definition_hash(level)
                    or binding["inputHash"] != captured["inputHash"]
                ):
                    raise Conflict(
                        "Build the current native planning table before previewing resolution."
                    )
                population = await metadata_repositories(repo).artifacts.require(
                    ArtifactLookup(artifact_id=binding["artifactId"]),
                    "Native population",
                )
            for sid in [
                candidate["definition"]["sourceId"],
                *calendar_source_ids(objects, candidate),
            ]:
                source = next(
                    document for document in objects if document["objectId"] == sid
                )
                source_snapshot = await self.planning.input_capture.capture_source(
                    repo, model, revision, source
                )
                captured["relations"][sid] = source_snapshot.artifact
                captured["managed"][sid] = source_snapshot.definition
            managed = {
                sid: artifact
                for sid, artifact in captured["relations"].items()
                if sid in captured["managed"]
            }
            inputs = {a["artifactId"]: a for a in [population, *managed.values()] if a}
            await self.read_leases.acquire(
                repo,
                read_id,
                inputs.values(),
                resource="Resolution input",
                retired_message="A resolution input was retired. Retry.",
            )
            return (
                objects,
                candidate,
                level,
                population,
                managed,
                captured["managed"],
                list(inputs),
                rev["version"],
            )

        (
            objects,
            tx,
            level,
            population,
            managed,
            definitions,
            aids,
            version,
        ) = await self.store.transaction(snapshot)

        async with self.read_leases.hold(
            read_id, aids, "Resolution read lease expired. Retry."
        ) as heartbeat:
            await self.planning.storage.ensure_inputs(
                [population] if population else []
            )
            await self.managed.input_verifier.verify(objects, managed, definitions)
            profiles = (
                {}
                if standalone
                else await prepare_resolution_profiles(
                    self.warehouse,
                    [
                        o
                        for o in objects
                        if o["kind"] != "transaction" or o["objectId"] == tx["objectId"]
                    ],
                    managed,
                    self.settings.resolution_max_profiles,
                    self.executor.execution_settings(),
                )
            )
            if standalone:
                compiler = CompilationContext(
                    self.settings.clickhouse_database,
                    objects,
                    managed,
                    resolution_max_rows=self.settings.resolution_max_rows,
                )
                compiler.max_nodes = self.settings.measure_max_nodes
                resolved = InputPipelineCompiler(compiler).compile(tx, target)
            else:
                compiler = MeasureCompiler(
                    self.settings.clickhouse_database,
                    objects,
                    {level["objectId"]: population} if level else {},
                    managed,
                    level["objectId"] if level else None,
                    resolution_profiles=profiles,
                    resolution_max_rows=self.settings.resolution_max_rows,
                )
                compiler.max_nodes = self.settings.measure_max_nodes
                resolved = compiler.resolve_transaction(tx["objectId"])
            preview = compile_resolution_preview(
                compiler,
                resolved,
                managed[tx["definition"]["sourceId"]]["physicalName"],
                len(profiles.get(tx["objectId"], [])),
                self.settings.measure_max_sql_bytes,
            )
            compiled = preview.execution
            await self.executor.checks(compiled)

            rows, diagnostics = await self.warehouse.queries.resolution_preview(
                preview.queries, self.executor.execution_settings()
            )
            await self.executor.checks(compiled)

            async def verify(repo):
                rev = await metadata_repositories(repo).revisions.require(
                    RevisionLookup(model_id=model, revision_id=revision), "Revision"
                )
                if rev["version"] != version:
                    raise Conflict(
                        "Metadata changed while resolving. Refresh and preview again."
                    )
                await self.read_leases.verify(
                    repo, read_id, aids, "Resolution read lease expired. Retry."
                )

            await self.store.transaction(verify)
            if heartbeat.done():
                heartbeat.result()
            return {
                "columns": resolved.columns,
                "rows": rows,
                "rowCount": diagnostics["resolvedRows"],
                "diagnostics": diagnostics,
                "limit": 50,
            }
