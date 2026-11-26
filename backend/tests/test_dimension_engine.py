from datetime import timedelta

import pytest

from backend.app.core.config import Settings
from backend.app.core.errors import Conflict
from backend.app.engine.execution.worker import EngineWorker
from backend.app.schemas.dimensions import DimensionInput
from backend.app.services.base import now
from backend.app.services.dimension_builds import DimensionBuildService
from backend.app.services.dimensions import DimensionService
from backend.tests.test_engine import context, submit


class FakePlanningExecutor:
    async def estimate(self, plan, compiler):
        return {"estimatedRows": 1}

    async def checks(self, compiled):
        pass

    async def build(self, artifact, compiled, plan):
        return {"rowCount": 1, "columns": list(plan.schema.columns), "bytesOnDisk": 10}


async def setup():
    store, managed, worker = await context()
    await submit(managed)
    await worker.execute(await worker.claim())

    async def verify(artifact, compiled):
        pass

    managed.executor.verify = verify
    managed.executor.warehouse = None
    builds = DimensionBuildService(store, Settings(_env_file=None), managed)
    builds.executor = FakePlanningExecutor()
    worker.register("dimension", builds)
    worker.register("dimensionPreview", builds)
    payload = DimensionInput.model_validate(
        {
            "objectId": "pl_main",
            "name": "Planning",
            "displayName": "Planning",
            "definition": {
                "population": {"operation": "source", "sourceId": "raw"},
                "grain": {
                    "keyIds": ["product"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"columnId": "product"},
                        }
                    ],
                },
                "populationAttributes": [
                    {
                        "columnId": "attribute",
                        "name": "attribute",
                        "populationRef": {"columnId": "attribute"},
                    }
                ],
            },
        }
    )
    await DimensionService(store).create("default_model", "revision_1", payload)
    return store, managed, worker, builds


@pytest.mark.asyncio
async def test_planning_idempotency_snapshots_and_fenced_recovery():
    store, managed, worker, builds = await setup()
    run = await builds.submit("default_model", "revision_1", "pl_main", 1, "build_once")
    assert (
        await builds.submit("default_model", "revision_1", "pl_main", 1, "build_once")
        == run
    )
    with pytest.raises(Conflict):
        await builds.submit("default_model", "revision_1", "pl_main", 2, "build_once")
    claimed = await worker.claim()
    await store.repository.replace(
        "engine_runs",
        {"runId": claimed["runId"]},
        {**claimed, "leaseUntil": now() - timedelta(seconds=1)},
    )
    new_worker = EngineWorker(
        store, handlers=worker.handlers, collector=worker.collector
    )
    recovered = await new_worker.claim()
    assert recovered["artifactId"] != claimed["artifactId"]
    assert recovered["fence"] == claimed["fence"] + 1
    original = await store.repository.get(
        "engine_artifacts", {"artifactId": claimed["artifactId"]}
    )
    replacement = await store.repository.get(
        "engine_artifacts", {"artifactId": recovered["artifactId"]}
    )
    assert original["physicalName"] == replacement["physicalName"]
    assert original["artifactId"] != replacement["artifactId"]
    assert "generationToken" not in original
    await worker.execute(claimed)
    assert (
        await store.repository.get(
            "engine_artifact_bindings",
            {"resourceKind": "dimension", "resourceId": "pl_main"},
        )
        is None
    )
    await new_worker.execute(recovered)
    assert (await builds.status("default_model", "revision_1", "pl_main"))[
        "state"
    ] == "ready"
    assert (await managed.run_status("default_model", "revision_1", run["runId"]))[
        "state"
    ] == "succeeded"
    assert store.documents["engine_artifact_pins"]


@pytest.mark.asyncio
async def test_changed_definition_or_dependency_keeps_previous_table():
    store, managed, worker, builds = await setup()
    await builds.submit("default_model", "revision_1", "pl_main", 1, "first")
    await worker.execute(await worker.claim())
    previous = await store.repository.get(
        "engine_artifact_bindings",
        {"resourceKind": "dimension", "resourceId": "pl_main"},
    )
    run = await builds.submit("default_model", "revision_1", "pl_main", 1, "stale")
    claimed = await worker.claim()
    await store.transaction(
        lambda repo: builds.touch_revision(repo, "default_model", "revision_1")
    )
    await worker.execute(claimed)
    assert (await managed.run_status("default_model", "revision_1", run["runId"]))[
        "state"
    ] == "superseded"
    assert previous == await store.repository.get(
        "engine_artifact_bindings",
        {"resourceKind": "dimension", "resourceId": "pl_main"},
    )


def test_readable_table_names_are_bounded_and_revision_scoped():
    from backend.app.warehouse.current_storage import TableStorage

    identity = {
        "modelId": "model_1",
        "revisionId": "revision_1",
        "resourceKind": "dimension",
        "resourceId": "PLC",
    }
    population = TableStorage.physical_name(
        "Brand / Zone - Sold To", identity, "allocation_1"
    )
    assert population.startswith("grain_Brand_Zone_Sold_To_")
    values = TableStorage.physical_name(
        "PLC", {**identity, "resourceKind": "measureValues"}, "allocation_1"
    )
    assert values.startswith("grain_PLC_mat_")
    assert population != TableStorage.physical_name(
        "Brand / Zone - Sold To",
        {**identity, "revisionId": "revision_2"},
        "allocation_1",
    )
    assert population != TableStorage.physical_name(
        "Brand / Zone - Sold To", identity, "allocation_2"
    )
    assert len(TableStorage.physical_name("x" * 500, identity, "allocation_1")) <= 128


@pytest.mark.asyncio
async def test_preview_build_does_not_save_and_commit_reuses_the_built_generation():
    from copy import deepcopy

    store, managed, worker, builds = await setup()
    await builds.submit("default_model", "revision_1", "pl_main", 1, "initial_build")
    await worker.execute(await worker.claim())
    original = await store.repository.get("semantic_objects", {"objectId": "pl_main"})
    revision = await store.repository.get(
        "semantic_revisions", {"revisionId": "revision_1"}
    )
    old_binding = await store.repository.get(
        "engine_artifact_bindings", {"resourceId": "pl_main"}
    )
    body = {
        k: deepcopy(original[k]) for k in DimensionInput.model_fields if k in original
    }
    body["name"] = "Changed dimension"
    payload = DimensionInput.model_validate(body)
    preview = await builds.submit(
        "default_model", "revision_1", "pl_main", 1, "preview_build", payload
    )
    await worker.execute(await worker.claim())
    run = await store.repository.get("engine_runs", {"runId": preview["runId"]})
    assert run["state"] == "succeeded", run
    assert (
        await store.repository.get("semantic_objects", {"objectId": "pl_main"})
    ) == original
    assert (
        await store.repository.get("semantic_revisions", {"revisionId": "revision_1"})
    ) == revision
    assert (
        await store.repository.get(
            "engine_artifact_bindings", {"resourceId": "pl_main"}
        )
    ) == old_binding
    before = await store.repository.get(
        "engine_artifacts", {"artifactId": old_binding["artifactId"]}
    )
    candidate = await store.repository.get(
        "engine_artifacts", {"artifactId": run["artifactId"]}
    )
    assert candidate["physicalName"] == before["physicalName"]
    assert candidate["artifactId"] != before["artifactId"]
    assert "generationToken" not in candidate
    committed = await builds.commit_draft(
        "default_model", "revision_1", run["runId"], payload
    )
    assert committed["version"] == 2 and committed["name"] == body["name"]
    binding = await store.repository.get(
        "engine_artifact_bindings", {"resourceId": "pl_main"}
    )
    assert binding["artifactId"] == candidate["artifactId"]
    assert (
        await builds.commit_draft("default_model", "revision_1", run["runId"], payload)
        == committed
    )


@pytest.mark.asyncio
async def test_new_preview_remains_unsaved_and_stale_commit_is_rejected():
    from copy import deepcopy

    store, managed, worker, builds = await setup()
    original = await store.repository.get("semantic_objects", {"objectId": "pl_main"})
    payload = DimensionInput.model_validate(
        {
            **{
                k: deepcopy(original[k])
                for k in DimensionInput.model_fields
                if k in original
            },
            "objectId": "unsaved_level",
            "name": "Unsaved",
        }
    )
    preview = await builds.submit(
        "default_model", "revision_1", "unsaved_level", None, "new_preview", payload
    )
    await worker.execute(await worker.claim())
    run = await store.repository.get("engine_runs", {"runId": preview["runId"]})
    assert run["state"] == "succeeded", run
    assert (
        await store.repository.get("semantic_objects", {"objectId": "unsaved_level"})
        is None
    )
    assert (
        await store.repository.get(
            "engine_artifact_bindings", {"resourceId": "unsaved_level"}
        )
        is None
    )
    revision = await store.repository.get(
        "semantic_revisions", {"revisionId": "revision_1"}
    )
    await store.repository.replace(
        "semantic_revisions",
        {"revisionId": "revision_1"},
        {**revision, "version": revision["version"] + 1},
    )
    with pytest.raises(Conflict):
        await builds.commit_draft("default_model", "revision_1", run["runId"], payload)
    assert (
        await store.repository.get("semantic_objects", {"objectId": "unsaved_level"})
        is None
    )
