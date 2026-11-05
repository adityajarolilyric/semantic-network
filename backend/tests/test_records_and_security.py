from fastapi.testclient import TestClient
from pydantic import SecretStr
from pymongo.errors import AutoReconnect

from backend.tests.fixture_app import create_fixture_app as create_app
from .conftest import ROOT
from .memory_store import MemoryStore
from .test_metadata_api import publish


def evidence(record_id="check_product_key"):
    return {
        "recordId": record_id,
        "objectIds": ["src_product"],
        "rule": "unique_key",
        "result": "unknown",
        "checkedAt": "2026-10-31T12:00:00Z",
        "sourceFingerprint": "schema_1",
        "scope": "declaration",
    }


def example():
    return {
        "recordId": "forecast_example",
        "name": "Forecast at PT",
        "objectIds": ["src_forecast", "measure_fqty"],
        "businessQuestion": "What is total forecast by product and day?",
        "rationale": "Sum across locations.",
        "fixtures": [
            {
                "sourceId": "src_forecast",
                "rows": [
                    {
                        "product_id": "p1",
                        "location_id": "l1",
                        "date": "2026-10-31",
                        "forecast_qty": "10",
                    }
                ],
            }
        ],
        "expectedGrainColumnIds": ["pt_product", "pt_date"],
        "expectedRows": [{"product_id": "p1", "date": "2026-10-31", "FQTY": "10"}],
    }


def test_evidence_crud_and_published_observations(populated):
    path = ROOT + "/evidence"
    payload = evidence()
    assert populated.post(path, json=payload).status_code == 201
    document_path = path + "/check_product_key"
    payload["description"] = "Awaiting a full warehouse check"
    assert (
        populated.put(
            document_path, json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert publish(populated).status_code == 200
    # Evidence is observed context, not a rewrite of the published definition.
    published_version = populated.get(ROOT).json()["version"]
    payload["description"] = "Still pending"
    assert (
        populated.put(
            document_path, json=payload, headers={"If-Match": '"2"'}
        ).status_code
        == 200
    )
    assert populated.get(ROOT).json()["version"] == published_version
    assert (
        populated.delete(document_path, headers={"If-Match": '"3"'}).status_code == 204
    )


def test_evidence_cannot_claim_declaration_passed(populated):
    payload = evidence()
    payload["result"] = "passed"
    assert populated.post(ROOT + "/evidence", json=payload).status_code == 422


def test_example_crud_and_reference_validation(populated):
    path = ROOT + "/examples"
    payload = example()
    assert populated.post(path, json=payload).status_code == 201
    payload["rationale"] = "Aggregate locations before comparing measures."
    resource = path + "/forecast_example"
    assert (
        populated.put(resource, json=payload, headers={"If-Match": '"1"'}).status_code
        == 200
    )
    assert len(populated.get(path).json()["items"]) == 1
    assert populated.delete(resource, headers={"If-Match": '"2"'}).status_code == 204
    payload["fixtures"][0]["rows"][0]["unknown_column"] = 1
    assert populated.post(path, json=payload).status_code == 422


def test_examples_are_frozen_and_cloned(populated):
    assert populated.post(ROOT + "/examples", json=example()).status_code == 201
    assert publish(populated).status_code == 200
    assert (
        populated.delete(
            ROOT + "/examples/forecast_example", headers={"If-Match": '"1"'}
        ).status_code
        == 409
    )
    model = populated.get("/api/v1/models/model_supply_planning").json()
    result = populated.post(
        "/api/v1/models/model_supply_planning/revisions",
        json={
            "revisionId": "rev_2",
            "parentRevisionId": "rev_1",
            "expectedModelVersion": model["version"],
        },
    )
    assert result.status_code == 201
    assert (
        populated.get(
            ROOT.replace("rev_1", "rev_2") + "/examples/forecast_example"
        ).status_code
        == 200
    )
    assert (
        populated.get(ROOT.replace("rev_1", "rev_2") + "/evidence").json()["items"]
        == []
    )


def test_contract_crud(client):
    payload = {
        "contractId": "formula_v1",
        "name": "Formula contract",
        "contractVersion": 1,
        "description": "Formula AST vocabulary",
        "definition": {"functions": ["abs"]},
    }
    assert client.post("/api/v1/contracts", json=payload).status_code == 201
    path = "/api/v1/contracts/formula_v1"
    assert client.get(path).json()["definition"]["functions"] == ["abs"]
    payload["description"] = "Updated documentation"
    assert (
        client.put(path, json=payload, headers={"If-Match": '"1"'}).status_code == 200
    )
    assert len(client.get("/api/v1/contracts").json()["items"]) == 1
    assert client.delete(path, headers={"If-Match": '"2"'}).status_code == 204


def test_bearer_auth(settings):
    settings.api_key = SecretStr("test-secret")
    with TestClient(create_app(settings, MemoryStore())) as client:
        assert client.get("/api/v1/models").status_code == 401
        assert (
            client.get(
                "/api/v1/models", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/api/v1/models", headers={"Authorization": "Bearer test-secret"}
            ).status_code
            == 200
        )
        assert client.get("/health/live").status_code == 200


def test_production_requires_auth(settings):
    settings.environment = "production"
    try:
        create_app(settings, MemoryStore())
        assert False, "production accepted a missing API key"
    except ValueError:
        pass


def test_body_limit_and_sanitized_errors(settings):
    settings.max_request_bytes = 1024
    with TestClient(create_app(settings, MemoryStore())) as client:
        assert client.post("/api/v1/models", content=b"x" * 2048).status_code == 413
        response = client.post(
            "/api/v1/models", json={"password": "should-not-be-reflected"}
        )
        assert response.status_code == 422
        assert "should-not-be-reflected" not in response.text
        assert response.json()["error"]["requestId"] == response.headers["x-request-id"]


def test_readiness_storage_failure_is_sanitized(settings):
    class BrokenStore(MemoryStore):
        async def ping(self):
            raise AutoReconnect("mongodb://secret-user:secret-pass@internal-host")

    with TestClient(create_app(settings, BrokenStore())) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert "secret-pass" not in response.text
        assert client.get("/health/live").status_code == 200
