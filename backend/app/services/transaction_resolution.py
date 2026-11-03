import asyncio
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

from ..core.errors import Conflict, MetadataError
from ..engine.date_range_resolution import calendar_source_ids
from ..engine.filters import dimension_filter_columns
from ..engine.contracts import Check, ExecutionPlan, RelationSchema
from ..engine.measure_compiler import MeasureCompiler
from ..engine.relationships import Coordinate, LevelRelationshipAnalyzer
from ..engine.transaction_resolution import (
    TransactionResolutionPlanner,
    prepare_resolution_profiles,
)
from ..warehouse.repository import quote_identifier as q
from .base import Service, now, scope


class TransactionResolutionService(Service):
    def __init__(self, store, settings, planning, managed):
        super().__init__(store, settings.max_objects_per_revision)
        self.settings, self.planning, self.managed = settings, planning, managed
        self.executor, self.warehouse = planning.executor, planning.executor.warehouse

    async def options(self, model, revision, level_id):
        repo = self.store.repository
        objects = await self.objects(repo, model, revision)
        level = await self.require(
            repo,
            "semantic_objects",
            {**scope(model, revision), "objectId": level_id, "kind": "planningLevel"},
            "Native planning level",
        )
        rel = LevelRelationshipAnalyzer(objects)
        binding = await repo.get(
            "engine_artifact_bindings",
            {
                **scope(model, revision),
                "resourceKind": "planningLevel",
                "resourceId": level_id,
            },
        )
        artifact = (
            await repo.get("engine_artifacts", {"artifactId": binding["artifactId"]})
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
            for cid in rel.outputs(level_id)
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
                if col["columnId"] in exposed and rel.resolve(level_id, coordinate):
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
        outputs = rel.outputs(level_id)
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

    async def preview(
        self,
        model,
        revision,
        payload,
        *,
        calendar_only=False,
        input_only=False,
        dimension_only=False,
    ):
        standalone = calendar_only or input_only or dimension_only
        read_id = "read_" + uuid4().hex

        async def snapshot(repo):
            rev = await self.require(
                repo, "semantic_revisions", scope(model, revision), "Revision"
            )
            objects = await self.objects(repo, model, revision)
            candidate = {**payload.model_dump(), **scope(model, revision), "version": 0}
            if calendar_only:
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
            if input_only:
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
                captured = await self.planning.capture(
                    repo, model, revision, level, objects
                )
                binding = await self.require(
                    repo,
                    "engine_artifact_bindings",
                    {
                        **scope(model, revision),
                        "resourceKind": "planningLevel",
                        "resourceId": level["objectId"],
                    },
                    "Built native planning table",
                )
                if (
                    binding["definitionHash"] != self.planning.definition_hash(level)
                    or binding["inputHash"] != captured["inputHash"]
                ):
                    raise Conflict(
                        "Build the current native planning table before previewing resolution."
                    )
                population = await self.require(
                    repo,
                    "engine_artifacts",
                    {"artifactId": binding["artifactId"]},
                    "Native population",
                )
            for sid in [
                candidate["definition"]["sourceId"],
                *calendar_source_ids(objects, candidate),
            ]:
                fake = {
                    "objectId": "capture",
                    "definition": {
                        "population": {
                            "operation": "source",
                            "sourceId": sid,
                        },
                        "dimensionBindings": [],
                    },
                }
                source_capture = await self.planning.capture(
                    repo, model, revision, fake, objects
                )
                captured["relations"].update(source_capture["relations"])
                captured["managed"].update(source_capture["managed"])
            managed = {
                sid: artifact
                for sid, artifact in captured["relations"].items()
                if sid in captured["managed"]
            }
            inputs = {a["artifactId"]: a for a in [population, *managed.values()] if a}
            for aid in inputs:
                current = await self.require(
                    repo, "engine_artifacts", {"artifactId": aid}, "Resolution input"
                )
                if current["state"] != "ready":
                    raise Conflict("A resolution input was retired. Retry.")
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": aid},
                    {**current, "pinVersion": current.get("pinVersion", 0) + 1},
                )
                await repo.insert(
                    "engine_read_leases",
                    {
                        "readId": read_id,
                        "artifactId": aid,
                        "expiresAt": now() + timedelta(seconds=180),
                    },
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

        async def renew():
            while True:
                await asyncio.sleep(15)

                async def update(repo):
                    for aid in aids:
                        identity = {"readId": read_id, "artifactId": aid}
                        lease = await repo.get("engine_read_leases", identity)
                        if not lease or lease["expiresAt"] <= now():
                            raise Conflict("Resolution read lease expired. Retry.")
                        await repo.replace(
                            "engine_read_leases",
                            identity,
                            {**lease, "expiresAt": now() + timedelta(seconds=180)},
                        )

                await self.store.transaction(update)

        heartbeat = asyncio.create_task(renew())
        try:
            await self.planning.storage.ensure_inputs(
                [population] if population else []
            )
            by_id = {o["objectId"]: o for o in objects}
            for sid, artifact in managed.items():
                source = by_id[sid]
                await self.managed.executor.inspect(source)
                compiled = self.managed.compiler.compile(
                    self.managed.planner.plan(
                        source,
                        self.managed.input_definition(definitions[sid]["definition"]),
                    )
                )
                await self.managed.executor.verify(artifact, compiled)
                await self.managed.executor.checks(compiled)
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
            if input_only or dimension_only:
                from ..engine.transaction_resolution import ResolvedTransaction

                source_table, input_columns = compiler.object_input(
                    tx, managed[tx["definition"]["sourceId"]]
                )
                if dimension_only:
                    lookup = {c["columnId"]: c for c in input_columns}
                    cols = tx["definition"]["columns"]
                    if any(c["sourceColumnId"] not in lookup for c in cols):
                        raise MetadataError(
                            "unknown_column",
                            "A dimension column is missing from the transformed input.",
                        )
                    node = compiler.cte(
                        "SELECT "
                        + ", ".join(
                            q(lookup[c["sourceColumnId"]]["name"])
                            + " AS "
                            + q(c["columnId"])
                            for c in cols
                        )
                        + " FROM "
                        + source_table
                    )
                    if tx["definition"].get("filters"):
                        node = compiler.cte(
                            "SELECT * FROM "
                            + q(node)
                            + " WHERE "
                            + compiler.filter_sql(
                                tx["definition"]["filters"],
                                dimension_filter_columns(
                                    tx["definition"], input_columns
                                ),
                            )
                        )
                    allowed = set(tx["definition"]["keyColumnIds"]) | set(
                        tx["definition"]["attributeColumnIds"]
                    )
                    columns = [
                        {
                            **lookup[c["sourceColumnId"]],
                            "columnId": c["columnId"],
                            "name": c["displayName"],
                        }
                        for c in cols
                        if c["columnId"] in allowed
                    ]
                else:
                    columns = input_columns
                    node = compiler.cte(
                        "SELECT "
                        + ", ".join(
                            q(c["name"]) + " AS " + q(c["columnId"]) for c in columns
                        )
                        + " FROM "
                        + source_table
                    )
                resolved = ResolvedTransaction(node, columns, [], node, [node], node)
            elif calendar_only:
                from ..engine.calendar_resolution import CalendarResolutionCompiler
                from ..engine.transaction_resolution import ResolvedTransaction

                calendar = CalendarResolutionCompiler(compiler, tx)
                node = calendar.compile()
                resolved = ResolvedTransaction(
                    node, calendar.p.schema(), [], node, [node], node
                )
            else:
                resolved = compiler.resolve_transaction(tx["objectId"])
            from sqlglot import parse_one

            sql = compiler.with_sql(
                "SELECT "
                + ", ".join(q(c["columnId"]) for c in resolved.columns)
                + " FROM "
                + q(resolved.node)
            )
            if len(sql.encode()) > self.settings.measure_max_sql_bytes:
                raise MetadataError(
                    "resolution_sql_limit",
                    "The resolution plan is too complex. Reduce matching profiles.",
                )
            checks = tuple(
                Check(c.code, compiler.with_sql(c.sql)) for c in compiler.assertions
            )
            compiled = ExecutionPlan(
                parse_one(sql, read="clickhouse"),
                checks,
                RelationSchema(tuple(resolved.columns)),
            )
            await self.executor.checks(compiled)

            def execute(connect):
                client = connect()
                rows = client.query(
                    sql + " ORDER BY ALL LIMIT 50",
                    settings=self.executor.execution_settings(),
                ).result_rows

                def count(node):
                    return int(
                        client.command(
                            compiler.with_sql("SELECT count() FROM " + q(node)),
                            settings=self.executor.execution_settings(),
                        )
                    )

                input_rows = int(
                    client.command(
                        "SELECT count() FROM "
                        + self.warehouse.qualify(
                            managed[tx["definition"]["sourceId"]]["physicalName"]
                        ),
                        settings=self.executor.execution_settings(),
                    )
                )
                expanded_inputs = (
                    count(resolved.input_node) if resolved.input_node else input_rows
                )
                matched = count(resolved.matches)
                total = count(resolved.node)
                winners = (
                    count(resolved.unfiltered_node)
                    if resolved.unfiltered_node
                    else total
                )
                latest = sum(count(node) for node in resolved.latest_nodes)
                return [[self.warehouse.cell(v) for v in row] for row in rows], {
                    "inputRows": input_rows,
                    "expandedInputRows": expanded_inputs,
                    "profiles": len(profiles.get(tx["objectId"], [])),
                    "matchedIntersections": matched,
                    "resolvedRows": total,
                    "overwrittenMatches": matched - winners,
                    "filteredOutRows": winners - total,
                    "shadowedAssignments": max(0, expanded_inputs - latest),
                }

            rows, diagnostics = await self.warehouse.run(execute)
            await self.executor.checks(compiled)

            async def verify(repo):
                rev = await self.require(
                    repo, "semantic_revisions", scope(model, revision), "Revision"
                )
                if rev["version"] != version:
                    raise Conflict(
                        "Metadata changed while resolving. Refresh and preview again."
                    )
                for aid in aids:
                    lease = await repo.get(
                        "engine_read_leases", {"readId": read_id, "artifactId": aid}
                    )
                    if not lease or lease["expiresAt"] <= now():
                        raise Conflict("Resolution read lease expired. Retry.")

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
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat

            async def release(repo):
                for aid in aids:
                    await repo.delete(
                        "engine_read_leases", {"readId": read_id, "artifactId": aid}
                    )

            await self.store.transaction(release)
