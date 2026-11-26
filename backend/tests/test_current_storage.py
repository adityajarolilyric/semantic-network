import asyncio
from unittest.mock import AsyncMock
import pytest
from backend.app.engine.execution.artifacts.recovery import ArtifactRecoveryLease
from backend.app.core.errors import MetadataError
from backend.app.warehouse.current_storage import TableStorage
from backend.tests.memory_store import MemoryStore
from backend.app.engine.execution.storage.layout import sorting_key
from backend.app.warehouse.sql_trace import (
    SqlTrace,
    bound_statement,
    capture_sql,
    current_trace,
)


def test_sorting_is_time_first_then_cardinality_with_stable_ties():
    assert sorting_key(
        ["product", "location", "week", "customer"],
        {"product": 50, "location": 5, "week": 100, "customer": 50},
        {"week"},
    ) == ["week", "location", "product", "customer"]
    assert sorting_key(["product", "week", "location"], {}, {"week"}) == [
        "week",
        "product",
        "location",
    ]


def test_native_parameters_do_not_replace_strings_or_comments():
    sql = (
        "SELECT {name:String}, '{name:String}', {values:Array(String)} -- {name:String}"
    )
    rendered = bound_statement(
        {"sql": sql, "parameters": {"name": "é'quoted", "values": ["a", "b"]}}
    )
    assert "'{name:String}'" in rendered
    assert "-- {name:String}" in rendered
    assert "CAST(['a', 'b'] AS Array(String))" in rendered
    assert "é" in rendered


def test_sql_trace_is_bounded_and_context_local():
    assert current_trace() is None
    with capture_sql(2) as trace:
        assert current_trace() is trace
        trace.record("query", "SELECT 1", {}, {})
        assert trace.omitted == 1
    assert current_trace() is None
    assert not SqlTrace(1).statements


async def test_reservation_requires_explicit_migration_for_legacy_storage():
    store = MemoryStore()
    await store.repository.insert(
        "engine_artifacts",
        {"artifactId": "old", "kind": "table", "storageMode": "versioned"},
    )
    await store.repository.insert(
        "engine_artifact_bindings",
        {
            "modelId": "m",
            "revisionId": "r",
            "resourceId": "d",
            "resourceKind": "dimension",
            "artifactId": "old",
        },
    )
    storage = TableStorage(store, None)
    with pytest.raises(MetadataError) as caught:
        await storage.reserve(
            store.repository,
            {"modelId": "m", "revisionId": "r", "resourceId": "d"},
            "Dimension",
        )
    assert caught.value.code == "storage_migration_required"
    assert not store.documents.get("engine_table_resources", [])


async def test_concurrent_recovery_only_one_worker_undoes_publication():
    store = MemoryStore()
    await store.repository.insert(
        "engine_artifacts", {"artifactId": "candidate", "cleanupPending": True}
    )
    lease = ArtifactRecoveryLease(store)
    recovered = []

    async def recover():
        async with lease.hold("candidate") as document:
            if document:
                recovered.append(document["cleanupOwner"])
                await asyncio.sleep(0.02)
                latest = await store.repository.get(
                    "engine_artifacts", {"artifactId": "candidate"}
                )
                await store.repository.replace(
                    "engine_artifacts",
                    {"artifactId": "candidate"},
                    {**latest, "cleanupPending": False},
                )

    await asyncio.gather(recover(), recover())
    assert len(recovered) == 1


async def test_successful_edit_recovers_an_unpublished_cell_stage_immediately(
    monkeypatch,
):
    store = MemoryStore()
    stage = {
        "artifactId": "stage",
        "modelId": "m",
        "revisionId": "r",
        "runId": "edit",
        "resourceKind": "editCells",
        "state": "candidate",
        "storageMode": "current",
        "physicalName": "__semantic_edit_build_stage",
        "cleanupPending": True,
        "attempt": 1,
    }
    await store.repository.insert("engine_artifacts", stage)
    await store.repository.insert(
        "engine_runs", {"runId": "edit", "state": "succeeded", "fence": 1}
    )
    storage = TableStorage(store, None)
    rollback = AsyncMock()
    monkeypatch.setattr(storage, "rollback", rollback)
    await storage.recover("m", "r")
    rollback.assert_awaited_once_with(stage)
