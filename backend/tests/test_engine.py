from copy import deepcopy
from datetime import timedelta

import pytest

from backend.app.core.config import Settings
from backend.app.core.errors import Conflict, MetadataError
from backend.app.engine.compiler import ClickHouseCompiler
from backend.app.engine.planner import ManagedTablePlanner
from backend.app.engine.worker import EngineWorker
from backend.app.schemas.managed_tables import ManagedDefinition
from backend.app.schemas.sources import SourceInput
from backend.app.services.base import now, scope
from backend.app.services.managed_tables import ManagedTableService
from backend.app.services.sources import SourceService
from backend.app.services.workspace import WorkspaceService
from backend.tests.memory_store import MemoryStore

SOURCE = {
    "objectId": "raw",
    "name": "Raw",
    "kind": "source",
    "definition": {
        "tableName": "Raw",
        "inScope": False,
        "keyColumns": [],
        "columns": [
            {
                "columnId": "product",
                "name": "product",
                "physicalType": "String",
                "nullable": False,
            },
            {
                "columnId": "attribute",
                "name": "attribute",
                "physicalType": "String",
                "nullable": False,
            },
            {
                "columnId": "value",
                "name": "value",
                "physicalType": "Int64",
                "nullable": True,
            },
        ],
    },
}
SPEC = {
    "pipeline": [
        {
            "stageId": "stage_main",
            "groupByColumnIds": ["product"],
            "pairs": [
                {
                    "pairId": "pair_main",
                    "attributeColumnId": "attribute",
                    "valueColumnId": "value",
                    "outputs": [{"attributeValue": "FQTY", "name": "FQTY"}],
                }
            ],
        }
    ]
}


def test_stable_column_identity_alias_order_revision_and_literal_type():
    planner = ManagedTablePlanner()
    first = planner.plan(SOURCE, ManagedDefinition.model_validate(SPEC))
    renamed = deepcopy(SPEC)
    renamed["pipeline"][0]["pairs"][0]["outputs"][0]["name"] = "Forecast Quantity"
    second = planner.plan(
        {**SOURCE, "revisionId": "new_revision"},
        ManagedDefinition.model_validate(renamed),
    )
    assert first.schema.columns[1]["columnId"] == second.schema.columns[1]["columnId"]
    assert first.schema.columns[1]["nullable"]
    assert first.schema.columns[1]["physicalType"] == "Int64"
    changed = deepcopy(SPEC)
    changed["pipeline"][0]["pairs"][0]["outputs"][0]["attributeValue"] = 1
    numeric = planner.plan(SOURCE, ManagedDefinition.model_validate(changed))
    changed["pipeline"][0]["pairs"][0]["outputs"][0]["attributeValue"] = "1"
    assert (
        numeric.schema.columns[1]["columnId"]
        != planner.plan(
            SOURCE, ManagedDefinition.model_validate(changed)
        ).schema.columns[1]["columnId"]
    )


def test_pipeline_and_names_are_allowlisted():
    spec = deepcopy(SPEC)
    spec["pipeline"][0]["pairs"][0]["outputs"][0]["name"] = "product"
    with pytest.raises(MetadataError):
        ManagedTablePlanner().plan(SOURCE, ManagedDefinition.model_validate(spec))
    spec = deepcopy(SPEC)
    spec["pipeline"][0]["pairs"][0]["outputs"][0]["columnId"] = "fake"
    with pytest.raises(MetadataError):
        ManagedTablePlanner().plan(SOURCE, ManagedDefinition.model_validate(spec))
    spec = ManagedDefinition.model_validate(SPEC)
    plan = ManagedTablePlanner().plan(SOURCE, spec)
    sql = ClickHouseCompiler("known_database").compile(plan).sql
    assert "MAX(" not in sql.upper() and "SUM(" not in sql.upper()
    assert "tuple(" in sql and "throwIf" in sql


class FakeExecutor:
    fail = False

    async def inspect(self, source):
        return "schema_fingerprint"

    async def checks(self, plan):
        if self.fail:
            raise MetadataError("duplicate_pivot_cell", "Duplicate cell")

    async def create(self, artifact, plan):
        pass


async def context():
    store = MemoryStore()
    await WorkspaceService(store).bootstrap()
    await SourceService(store).create(
        "default_model", "revision_1", SourceInput.model_validate(SOURCE)
    )
    service = ManagedTableService(store, Settings(_env_file=None), None, None)
    service.executor = FakeExecutor()
    return store, service, EngineWorker(service)


async def submit(service, spec=None, expected=0, key="request_key"):
    return await service.submit(
        "default_model",
        "revision_1",
        "raw",
        ManagedDefinition.model_validate(spec or {"pipeline": []}),
        expected,
        key,
    )


@pytest.mark.asyncio
async def test_idempotency_stale_inputs_cancel_and_recovery():
    store, service, worker = await context()
    run = await submit(service)
    assert await submit(service) == run
    with pytest.raises(Conflict):
        await submit(service, SPEC)
    claimed = await worker.claim()
    assert await worker.claim() is None
    expired = {**claimed, "leaseUntil": now() - timedelta(seconds=1)}
    await store.repository.replace("engine_runs", {"runId": run["runId"]}, expired)
    second_worker = EngineWorker(service)
    recovered = await second_worker.claim()
    assert recovered["fence"] == claimed["fence"] + 1
    with pytest.raises(Conflict):
        await worker.handlers["managedTable"].activate(claimed, {}, "", "", worker)
    await second_worker.execute(recovered)
    assert (await service.run_status("default_model", "revision_1", run["runId"]))[
        "state"
    ] == "succeeded"
    second = await submit(service, SPEC, 1, "second")
    claim = await worker.claim()
    await service.cancel("default_model", "revision_1", second["runId"])
    await worker.execute(claim)
    assert (await service.get("default_model", "revision_1", "raw"))["version"] == 1
    assert (await service.run_status("default_model", "revision_1", second["runId"]))[
        "state"
    ] == "cancelled"


@pytest.mark.asyncio
async def test_stale_revision_and_duplicate_failure_preserve_binding():
    store, service, worker = await context()
    await submit(service)
    await worker.execute(await worker.claim())
    old = await store.repository.get(
        "engine_artifact_bindings",
        {**scope("default_model", "revision_1"), "sourceId": "raw"},
    )
    stale = await submit(service, SPEC, 1, "stale")
    claim = await worker.claim()
    await service.store.transaction(
        lambda repo: service.touch_revision(repo, "default_model", "revision_1")
    )
    await worker.execute(claim)
    assert (await service.run_status("default_model", "revision_1", stale["runId"]))[
        "state"
    ] == "superseded"
    failed = await submit(service, SPEC, 1, "failed")
    service.executor.fail = True
    await worker.execute(await worker.claim())
    assert (await service.run_status("default_model", "revision_1", failed["runId"]))[
        "error"
    ]["code"] == "duplicate_pivot_cell"
    assert old == await store.repository.get(
        "engine_artifact_bindings",
        {**scope("default_model", "revision_1"), "sourceId": "raw"},
    )


@pytest.mark.asyncio
async def test_collection_retains_bound_published_artifact_and_drops_only_owned_unbound():
    from backend.app.engine.artifacts import ArtifactCollector

    store, service, worker = await context()
    await submit(service)
    await worker.execute(await worker.claim())
    await submit(service, SPEC, 1, "next_definition")
    await worker.execute(await worker.claim())
    artifacts = store.documents["engine_artifacts"]
    for artifact in artifacts:
        artifact["createdAt"] = now() - timedelta(days=2)

    class Warehouse:
        dropped = []

        async def run(self, fn):
            fn(lambda: self)

        def table_info(self, client, name):
            artifact = next(a for a in artifacts if a["physicalName"] == name)
            return ("View", "semanticArtifact:" + artifact["artifactId"])

        def qualify(self, name):
            return name

        def command(self, sql):
            self.dropped.append(sql)

    service.executor.warehouse = Warehouse()
    await ArtifactCollector(service).collect()
    assert len(service.executor.warehouse.dropped) == 1
    assert len([a for a in artifacts if a["state"] == "ready"]) == 1
    await ArtifactCollector(service).collect()
    assert len(service.executor.warehouse.dropped) == 1


@pytest.mark.asyncio
async def test_step_durations_use_the_current_attempt_and_restart_on_retry(monkeypatch):
    store, service, worker = await context()
    await submit(service)
    run = await worker.claim()
    clock = [now()]
    monkeypatch.setattr("backend.app.engine.worker.now", lambda: clock[0])
    await worker.step(run, "timed", "running")
    clock[0] += timedelta(milliseconds=1250)
    await worker.step(run, "timed", "succeeded")
    step = await store.repository.get(
        "engine_run_steps", {"runId": run["runId"], "stepId": "timed"}
    )
    assert step["durationMs"] == 1250
    assert step["updatedAt"] - step["startedAt"] == timedelta(milliseconds=1250)
    retried = {
        **await store.repository.get("engine_runs", {"runId": run["runId"]}),
        "fence": run["fence"] + 1,
    }
    await store.repository.replace("engine_runs", {"runId": run["runId"]}, retried)
    await worker.step(retried, "timed", "running")
    clock[0] += timedelta(milliseconds=500)
    await worker.step(retried, "timed", "succeeded")
    step = await store.repository.get(
        "engine_run_steps", {"runId": run["runId"], "stepId": "timed"}
    )
    assert step["durationMs"] == 500
    assert step["attempt"] == retried["fence"]
