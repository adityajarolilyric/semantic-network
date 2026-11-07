import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.tests.fixture_app import create_fixture_app as create_app
from .memory_store import MemoryStore

EXAMPLE = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "plt_pt.json").read_text()
)
MODEL = "model_supply_planning"
REVISION = "rev_1"
ROOT = f"/api/v1/models/{MODEL}/revisions/{REVISION}"
RESOURCES = {
    "source": "sources",
    "dimensionMembers": "dimension-members",
    "dimension": "dimensions",
    "transaction": "transactions",
    "measure": "measures",
}


def object_payload(document):
    return deepcopy(
        {
            key: value
            for key, value in document.items()
            if key
            not in {"_id", "modelId", "revisionId", "version", "createdAt", "updatedAt"}
        }
    )


def seed(client):
    model = EXAMPLE["semantic_models"][0]
    payload = {
        "modelId": model["_id"],
        "name": model["name"],
        "displayName": model["displayName"],
        "description": model["description"],
    }
    response = client.post("/api/v1/models", json=payload)
    assert response.status_code == 201, response.text
    response = client.post(
        f"/api/v1/models/{MODEL}/revisions",
        json={
            "revisionId": REVISION,
            "expectedModelVersion": 1,
            "baseDimensionId": "pl_plt",
        },
    )
    assert response.status_code == 201, response.text
    for document in EXAMPLE["semantic_objects"]:
        response = client.post(
            f"{ROOT}/{RESOURCES[document['kind']]}", json=object_payload(document)
        )
        assert response.status_code == 201, response.text


@pytest.fixture
def settings():
    return Settings(_env_file=None, environment="test")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, MemoryStore())) as client:
        yield client


@pytest.fixture
def populated(client):
    seed(client)
    return client
