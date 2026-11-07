import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import MetadataError
from backend.tests.fixture_app import create_fixture_app as create_app
from backend.app.services.workspace_gate import WorkspaceGate
from .conftest import seed
from .fake_warehouse import FakeWarehouse
from .memory_store import MemoryStore
from .test_metadata_api import publish
from .test_csv_sources import upload


def test_reset_clears_published_models_data_and_engine_state(settings):
    store, warehouse = MemoryStore(), FakeWarehouse()
    with TestClient(create_app(settings, store, warehouse)) as client:
        seed(client)
        assert publish(client).status_code == 200
        assert upload(client, b"id\n1\n").status_code == 201
        response = client.post("/api/v1/workspace/reset", json={"confirmed": True})
        assert response.status_code == 200, response.text
        assert response.json()["deletedTables"] == 1
        models = client.get("/api/v1/models").json()["items"]
        assert len(models) == 1 and models[0]["modelId"] == "default_model"
        assert models[0]["publishedRevisionId"] is None
        revisions = client.get("/api/v1/models/default_model/revisions").json()["items"]
        assert len(revisions) == 1 and revisions[0]["approvalStatus"] == "draft"
        assert revisions[0]["parentRevisionId"] is None
        assert revisions[0]["baseDimensionId"] is None
        assert not store.documents.get("semantic_objects")
        assert not store.documents.get("engine_artifacts")
        assert not store.documents.get("engine_runs")
        assert not client.get("/api/v1/warehouse/tables").json()["tables"]
        # Repeating a deliberate reset is safe and still leaves exactly one draft.
        assert (
            client.post("/api/v1/workspace/reset", json={"confirmed": True}).status_code
            == 200
        )


def test_reset_requires_confirmation_and_failure_is_retryable(settings):
    warehouse = FakeWarehouse()
    with TestClient(create_app(settings, MemoryStore(), warehouse)) as client:
        client.post("/api/v1/workspace/bootstrap")
        assert client.post("/api/v1/workspace/reset", json={}).status_code == 422
        assert (
            client.post(
                "/api/v1/workspace/reset", json={"confirmed": False}
            ).status_code
            == 422
        )
        warehouse.available = False
        assert (
            client.post("/api/v1/workspace/reset", json={"confirmed": True}).status_code
            == 503
        )
        assert client.post("/api/v1/workspace/bootstrap").status_code == 503
        warehouse.available = True
        assert (
            client.post("/api/v1/workspace/reset", json={"confirmed": True}).status_code
            == 200
        )
        assert client.post("/api/v1/workspace/bootstrap").status_code == 200


@pytest.mark.asyncio
async def test_reset_fences_old_transactions_and_waits_for_active_work():
    store = MemoryStore()
    gate = WorkspaceGate(store)
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation():
        async with gate.shared():
            entered.set()
            await release.wait()
            with pytest.raises(MetadataError, match="workspace"):
                await store.transaction(
                    lambda repo: repo.insert("semantic_models", {"modelId": "stale"})
                )

    task = asyncio.create_task(operation())
    await entered.wait()
    await gate.begin_reset(uuid4().hex)
    with pytest.raises(MetadataError):
        async with gate.shared():
            pass
    drained = asyncio.create_task(gate.drain())
    await asyncio.sleep(0.02)
    assert not drained.done()
    release.set()
    await task
    await drained
    assert not store.documents.get("semantic_models")
