from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..domain.dimensions import dimension_columns
from ..domain.transaction_measures import rebind_transaction_measures
from ..engine.calendar_resolution import CalendarResolutionPlanner
from ..engine.compilation_context import CompilationContext
from ..engine.date_range_resolution import calendar_source_ids
from ..engine.input_pipeline import InputPipelineCompiler, PreviewTarget
from ..engine.measure_compiler import MeasureCompiler
from ..engine.object_inputs import input_columns
from ..engine.preview_plans import compile_resolution_preview
from ..engine.read_leases import ArtifactReadLeases
from ..engine.relationships import Coordinate, LevelRelationshipAnalyzer
from ..engine.transaction_resolution import (
    TransactionResolutionPlanner,
    prepare_resolution_profiles,
)
from ..repositories.metadata import metadata_repositories
from ..repositories.metadata.criteria import (
    ObjectLookup,
    RevisionLookup,
)
from ..validation.graph import SemanticGraphValidator
from ..engine.virtual_populations import logical_population_query
from .base import Service, scope


class TransactionResolutionService(Service):
    def __init__(self, store, settings, populations, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.populations, self.managed = settings, populations, managed
        self.read_leases = ArtifactReadLeases(store)
        self.executor, self.warehouse = (
            populations.executor,
            populations.executor.warehouse,
        )

    async def options(self, model, revision, level_id=None, *, population=None):
        repo = self.store.repository
        objects = await self.graph_objects(repo, model, revision)
        if population is not None:
            level_id = population.objectId
            objects = await self.effective(
                repo,
                model,
                revision,
                [obj for obj in objects if obj["objectId"] != level_id]
                + [population.model_dump()],
            )
        level = (
            population.model_dump()
            if population is not None
            else (
                await metadata_repositories(repo).objects.require(
                    ObjectLookup(
                        model_id=model,
                        revision_id=revision,
                        object_id=level_id,
                        kind="dimension",
                    ),
                    "Native dimension",
                )
                if level_id
                else {"definition": {"grain": {"keyIds": [], "columns": []}}}
            )
        )
        rel = LevelRelationshipAnalyzer(objects)
        names = {
            column["columnId"]: column["name"]
            for column in (dimension_columns(level, objects) if level_id else [])
        }
        targets = [
            {
                "target": {"memberCatalogId": None, "columnId": cid},
                "label": "Native · " + names.get(cid, cid),
            }
            for cid in (rel.outputs(level_id) if level_id else {})
            if cid in names
        ]
        targets_by_id = {item["target"]["columnId"]: item for item in targets}
        for dim in rel.dimension_members.values():
            exposed = set(dim["definition"]["keyColumnIds"]) | set(
                dim["definition"]["attributeColumnIds"]
            )
            for col in dim["definition"]["columns"]:
                coordinate = Coordinate(
                    frozenset(
                        {
                            ("dimensionMembers", dim["objectId"], col["columnId"]),
                            (
                                "source",
                                dim["definition"]["sourceId"],
                                col["sourceColumnId"],
                            ),
                        }
                    )
                )
                resolved = rel.resolve(level_id, coordinate) if level_id else None
                if (
                    col["columnId"] in exposed
                    and resolved
                    and resolved.column_id in targets_by_id
                ):
                    # Preserve saved lookup references only as aliases of an actual
                    # native output; reachable but unselected attributes are not choices.
                    targets_by_id[resolved.column_id].setdefault("aliases", []).append(
                        {
                            "memberCatalogId": dim["objectId"],
                            "columnId": col["columnId"],
                        }
                    )
        calendars = []
        outputs = rel.outputs(level_id) if level_id else {}
        for dim in rel.dimension_members.values():
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
                        ("dimensionMembers", dim["objectId"], col["columnId"]),
                        ("source", d["sourceId"], col["sourceColumnId"]),
                    }:
                        suggestions[key] = col["columnId"]
            calendars.append(
                {
                    "memberCatalogId": dim["objectId"],
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

    async def schema(
        self, model, revision, transaction_id=None, payload=None, *, population=None
    ):
        repo = self.store.repository
        objects = await self.effective(
            repo, model, revision, await self.graph_objects(repo, model, revision)
        )
        if population is not None:
            objects = await self.effective(
                repo,
                model,
                revision,
                [obj for obj in objects if obj["objectId"] != population.objectId]
                + [population.model_dump()],
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
            tx = payload.model_dump(exclude={"measures"})
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
            return {
                "columns": CalendarResolutionPlanner(
                    objects, tx, source["definition"]["columns"]
                ).schema()
            }
        planner = TransactionResolutionPlanner(
            objects, tx, source["definition"]["columns"]
        )

        types = SemanticGraphValidator(objects)
        level = types.objects[tx["definition"]["dimensionId"]]
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
        repo = self.store.repository
        objects = await self.effective(
            repo, model, revision, await self.graph_objects(repo, model, revision)
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
        repo = self.store.repository
        objects = await self.effective(
            repo, model, revision, await self.graph_objects(repo, model, revision)
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

    async def preview_input(self, model, revision, payload, *, sql_only=False):
        return await self._preview(
            model, revision, payload, PreviewTarget.PIVOT_INPUT, sql_only=sql_only
        )

    async def preview_calendar(self, model, revision, payload, *, sql_only=False):
        return await self._preview(
            model, revision, payload, PreviewTarget.CALENDAR_INPUT, sql_only=sql_only
        )

    async def preview_unmapped(self, model, revision, payload, *, sql_only=False):
        return await self._preview(
            model,
            revision,
            payload,
            PreviewTarget.UNMAPPED_TRANSACTION,
            sql_only=sql_only,
        )

    async def preview_dimension(self, model, revision, payload, *, sql_only=False):
        return await self._preview(
            model, revision, payload, PreviewTarget.DIMENSION_OUTPUT, sql_only=sql_only
        )

    async def preview(
        self, model, revision, payload, *, sql_only=False, population=None
    ):
        return await self._preview(
            model,
            revision,
            payload,
            PreviewTarget.TRANSACTION_OUTPUT,
            sql_only=sql_only,
            population_definition=population,
        )

    async def _preview(
        self,
        model,
        revision,
        payload,
        target,
        *,
        sql_only=False,
        population_definition=None,
    ):
        unbound = (
            target == PreviewTarget.TRANSACTION_OUTPUT
            and payload.definition.dimensionId is None
        )
        standalone = target != PreviewTarget.TRANSACTION_OUTPUT or unbound
        pipeline_target = PreviewTarget.UNMAPPED_TRANSACTION if unbound else target
        read_id = "read_" + uuid4().hex

        async def snapshot(repo):
            rev = await metadata_repositories(repo).revisions.require(
                RevisionLookup(model_id=model, revision_id=revision), "Revision"
            )
            objects = await self.graph_objects(repo, model, revision)
            if population_definition is not None:
                objects = [
                    obj
                    for obj in objects
                    if obj["objectId"] != population_definition.objectId
                ] + [
                    {
                        **population_definition.model_dump(),
                        **scope(model, revision),
                        "version": 0,
                    }
                ]
            candidate = {
                **payload.model_dump(exclude={"measures"}),
                **scope(model, revision),
                "version": 0,
            }
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
            previous = next(
                (
                    obj
                    for obj in objects
                    if obj["objectId"] == candidate["objectId"]
                    and obj["kind"] == "transaction"
                ),
                None,
            )
            objects = [o for o in objects if o["objectId"] != candidate["objectId"]] + [
                candidate
            ]
            native_compiled = None
            if target == PreviewTarget.TRANSACTION_OUTPUT:
                rebound = {
                    measure["objectId"]: measure
                    for measure in rebind_transaction_measures(
                        previous, candidate, objects
                    )
                }
                objects = await self.effective(
                    repo,
                    model,
                    revision,
                    [rebound.get(obj["objectId"], obj) for obj in objects],
                )
                self.validate(objects, rev["baseDimensionId"])
            if standalone:
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
                by_id = {o["objectId"]: o for o in objects}
                level = by_id[candidate["definition"]["dimensionId"]]
                captured = await self.populations.capture_population_inputs(
                    repo, model, revision, level, objects, virtual=True
                )
                plan = self.populations.planner.plan(
                    level, objects, captured["relations"]
                )
                native_compiled = self.populations.compiler.compile(plan)
                # Dimension output uses public names; resolution consumes logical IDs,
                # just as it does when reading a persisted population table.
                population = {
                    "artifactId": "virtual_" + captured["inputHash"],
                    "physicalName": "__dimension_preview",
                    "columns": list(plan.schema.columns),
                    "query": logical_population_query(native_compiled),
                }
            for sid in [
                candidate["definition"]["sourceId"],
                *calendar_source_ids(objects, candidate),
            ]:
                source = next(
                    document for document in objects if document["objectId"] == sid
                )
                source_snapshot = await self.populations.input_capture.capture_source(
                    repo, model, revision, source
                )
                captured["relations"][sid] = source_snapshot.artifact
                captured["managed"][sid] = source_snapshot.definition
            managed = {
                sid: artifact
                for sid, artifact in captured["relations"].items()
                if sid in captured["managed"]
            }
            inputs = {a["artifactId"]: a for a in managed.values()}
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
                native_compiled,
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
            native_compiled,
        ) = await self.store.transaction(snapshot)

        async with self.read_leases.hold(
            read_id, aids, "Resolution read lease expired. Retry."
        ) as heartbeat:
            await self.managed.input_verifier.verify(objects, managed, definitions)
            if native_compiled is not None and not sql_only:
                await self.executor.checks(native_compiled)
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
                resolved = InputPipelineCompiler(compiler).compile(tx, pipeline_target)
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
            if sql_only:
                return {"sql": compiled.formatted_sql, "dialect": "clickhouse"}
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
