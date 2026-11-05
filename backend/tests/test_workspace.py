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


def test_production_lifecycle_endpoints_are_removed(settings):
    with TestClient(create_app(settings, MemoryStore())) as client:
        model = client.post("/api/v1/workspace/bootstrap").json()
        root = "/api/v1/models/" + model["modelId"]
        revision = root + "/revisions/" + model["draftRevisionId"]
        schema = client.get("/openapi.json").json()["paths"]
        assert "post" not in schema["/api/v1/models"]
        assert "post" not in schema["/api/v1/models/{model_id}/revisions"]
        assert "delete" not in schema["/api/v1/models/{model_id}"]
        assert (
            "delete" not in schema["/api/v1/models/{model_id}/revisions/{revision_id}"]
        )
        assert "/api/v1/models/{model_id}/revisions/{revision_id}/publish" not in schema
        assert (
            client.post(
                "/api/v1/models", json={"modelId": "another", "name": "Another"}
            ).status_code
            == 405
        )
        assert (
            client.post(root + "/revisions", json={"revisionId": "another"}).status_code
            == 405
        )
        assert client.post(revision + "/publish", json={}).status_code in {404, 405}
        assert client.delete(root).status_code == 405
        assert client.delete(revision).status_code == 405
        assert client.post("/api/v1/workspace/bootstrap").json() == model
        assert len(client.get("/api/v1/models").json()["items"]) == 1
        assert len(client.get(root + "/revisions").json()["items"]) == 1
        assert client.get(revision).json()["approvalStatus"] == "draft"
