"""Transaction onboarding creates ordinary semantic objects, not a new mapping mode."""

from ..core.errors import Conflict, ImmutableRevision, MetadataError
from ..engine.contracts import digest
from ..schemas.planning_levels import PlanningLevelInput
from ..schemas.transactions import TransactionInput
from .base import Service, now, scope


class TransactionGrainService(Service):
    def __init__(self, store, settings, planning):
        super().__init__(store, settings.max_objects_per_revision)
        self.planning = planning

    async def definitions(self, repo, model, revision, payload):
        objects = await self.objects(repo, model, revision)
        effective = await self.effective(repo, model, revision, objects)
        source = next(
            (
                o
                for o in effective
                if o["objectId"] == payload.sourceId
                and o["kind"] == "source"
                and o["definition"]["inScope"]
            ),
            None,
        )
        if not source:
            raise MetadataError(
                "invalid_grain_source", "Choose an available Managed Table."
            )
        from ..engine.calendar_resolution import CalendarResolutionPlanner

        owner = {
            "objectId": payload.objectId,
            "definition": {
                "sourceId": payload.sourceId,
                "pivot": payload.pivot.model_dump() if payload.pivot else None,
                "calendarResolution": payload.calendarResolution.model_dump()
                if payload.calendarResolution
                else None,
            },
        }
        columns = {
            c["columnId"]: c
            for c in CalendarResolutionPlanner(
                effective, owner, source["definition"]["columns"]
            ).schema()
        }
        population = {"operation": "source", "sourceId": payload.sourceId}
        if payload.pivot or payload.calendarResolution:
            population["inputTransform"] = {
                "objectId": payload.objectId,
                "pivot": owner["definition"]["pivot"],
                "calendarResolution": owner["definition"]["calendarResolution"],
            }
        if not set(payload.grainColumnIds) <= columns.keys():
            raise MetadataError(
                "invalid_grain_column",
                "Choose grain columns from the transformed Transaction input.",
            )
        level_id = "pl_" + digest(["transaction-grain", payload.objectId])[:28]
        keys = ["g_" + digest([level_id, cid])[:24] for cid in payload.grainColumnIds]
        name = payload.planningLevelName or (payload.name + " grain")[:200]
        level = PlanningLevelInput.model_validate(
            {
                "objectId": level_id,
                "name": name,
                "displayName": name,
                "definition": {
                    "population": population,
                    "grain": {
                        "keyIds": keys,
                        "columns": [
                            {
                                "grainColumnId": key,
                                "name": columns[cid]["name"],
                                "populationRef": {"columnId": cid},
                            }
                            for key, cid in zip(keys, payload.grainColumnIds)
                        ],
                    },
                    "dimensionBindings": [],
                },
            }
        )
        transaction = TransactionInput.model_validate(
            {
                "objectId": payload.objectId,
                "name": payload.name,
                "displayName": payload.displayName,
                "description": payload.description,
                "definition": {
                    **owner["definition"],
                    "planningLevelId": level_id,
                    "keyMappings": [
                        {"grainColumnId": key, "sourceColumnId": cid}
                        for key, cid in zip(keys, payload.grainColumnIds)
                    ],
                },
            }
        )
        return objects, level, transaction

    async def preview(self, model, revision, payload):
        _, level, _ = await self.store.transaction(
            lambda repo: self.definitions(repo, model, revision, payload)
        )
        return await self.planning.preview(model, revision, level)

    async def create(self, model, revision, payload):
        async def operation(repo):
            rev = await self.require(
                repo, "semantic_revisions", scope(model, revision), "Revision"
            )
            if rev["approvalStatus"] != "draft":
                raise ImmutableRevision()
            objects, level, transaction = await self.definitions(
                repo, model, revision, payload
            )
            inputs = [level.model_dump(), transaction.model_dump()]
            existing = {o["objectId"]: o for o in objects}
            if any(o["objectId"] in existing for o in inputs):
                # Stable generated IDs make retries safe; never overwrite edited objects.
                if not all(
                    o["objectId"] in existing
                    and all(existing[o["objectId"]].get(k) == v for k, v in o.items())
                    for o in inputs
                ):
                    raise Conflict(
                        "The Transaction or generated planning level already exists with different metadata. Reload it before editing."
                    )
                return {
                    "transaction": existing[transaction.objectId],
                    "planningLevel": existing[level.objectId],
                }
            if len(objects) + 2 > self.max_objects:
                raise Conflict("The revision has reached its metadata object limit.")
            rev = await self.touch_revision(repo, model, revision)
            base = rev["basePlanningLevelId"]
            if base is None and not any(o["kind"] == "planningLevel" for o in objects):
                base = level.objectId
            timestamp = now()
            docs = [
                {
                    **o,
                    **scope(model, revision),
                    "version": 1,
                    "createdAt": timestamp,
                    "updatedAt": timestamp,
                }
                for o in inputs
            ]
            self.validate(
                await self.effective(repo, model, revision, [*objects, *docs]), base
            )
            for doc in docs:
                await repo.insert("semantic_objects", doc)
            if base != rev["basePlanningLevelId"]:
                await repo.replace(
                    "semantic_revisions",
                    scope(model, revision),
                    {**rev, "basePlanningLevelId": base},
                )
            return {"planningLevel": docs[0], "transaction": docs[1]}

        return await self.store.transaction(operation)
