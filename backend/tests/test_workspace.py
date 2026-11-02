from fastapi.testclient import TestClient

from backend.app.main import create_app
from .memory_store import MemoryStore


def test_default_workspace_is_durable_and_idempotent(settings):
    store = MemoryStore()
    with TestClient(create_app(settings, store)) as client:
        first = client.post("/api/v1/workspace/bootstrap")
        assert first.status_code == 200
        assert first.json()["draftRevisionId"] == "revision_1"
        assert client.post("/api/v1/workspace/bootstrap").json() == first.json()
        assert len(client.get("/api/v1/models").json()["items"]) == 1
        revision = client.get(
            "/api/v1/models/default_model/revisions/revision_1"
        ).json()
        assert revision["approvalStatus"] == "draft"
        assert revision["basePlanningLevelId"] is None
    with TestClient(create_app(settings, store)) as second:
        assert second.post("/api/v1/workspace/bootstrap").json() == first.json()


def test_bootstrap_preserves_existing_user_model(client):
    response = client.post(
        "/api/v1/models",
        json={"modelId": "custom", "name": "Custom", "displayName": "Custom"},
    )
    assert response.status_code == 201
    assert client.post("/api/v1/workspace/bootstrap").json()["modelId"] == "custom"
    assert client.get("/api/v1/models/default_model").status_code == 404
