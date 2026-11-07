# ruff: noqa: F811
"""Upload defaults exercised through the production import endpoint and real views."""

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE
from .test_measures_clickhouse import build_values, values
from backend.tests.test_csv_sources import upload

pytestmark = pytest.mark.integration


def test_upload_transaction_measures_build_and_replay(clickhouse_app):
    client, _, _, _ = clickhouse_app
    content = b"product,location,qty,value\nP1,L1,10,20.5\nP1,L1,20,30.5\nP2,L1,5,10\n"
    response = upload(client, content, table="AutoSales")
    assert response.status_code == 201, response.text
    assert response.json()["warnings"] == []
    source = client.get(BASE + "/sources").json()["items"][0]
    assert source["definition"]["inScope"]
    transactions = client.get(BASE + "/transactions").json()["items"]
    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction["name"] == "AutoSales"
    assert transaction["definition"]["grainColumnIds"] == ["col_1", "col_2"]
    assert transaction["definition"]["dimensionId"] is None
    measures = client.get(BASE + "/measures").json()["items"]
    assert {m["name"] for m in measures} == {"qty", "value"}
    for measure in measures:
        definition = measure["definition"]
        assert definition["transactionId"] == transaction["objectId"]
        assert definition["aggregation"]["nonTime"] == "sum"
        assert definition["aggregation"]["time"] == "sum"
        assert definition["editable"] is False
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, columns = values(client, transaction["effectiveGrainId"])
    assert sorted(float(row[columns["qty"]]) for row in result["rows"]) == [5, 30]
    # Replay preserves user edits, IDs, versions and the existing managed view.
    first = measures[0]
    payload = {
        k: v
        for k, v in first.items()
        if k not in {"version", "modelId", "revisionId", "createdAt", "updatedAt"}
    }
    payload["name"] = "Renamed quantity"
    updated = client.put(
        BASE + "/measures/" + first["objectId"],
        json=payload,
        headers={"If-Match": str(first["version"])},
    )
    assert updated.status_code == 200, updated.text
    response = upload(client, content, table="AutoSales")
    assert response.json()["replayed"]
    assert response.json()["warnings"] == []
    assert client.get(BASE + "/measures/" + first["objectId"]).json() == updated.json()
    assert len(client.get(BASE + "/transactions").json()["items"]) == 1


def test_upload_non_numeric_dimension_and_numeric_only_warning(clickhouse_app):
    client, _, _, _ = clickhouse_app
    response = upload(
        client, b"product,brand,active\nP1,B1,true\nP2,B1,false\n", table="AutoProduct"
    )
    assert response.status_code == 201, response.text
    assert response.json()["warnings"] == []
    dimensions = client.get(BASE + "/dimensions").json()["items"]
    assert len(dimensions) == 1
    dimension = dimensions[0]
    assert dimension["name"] == "AutoProduct"
    assert dimension["definition"]["grain"]["keyIds"] == ["col_1", "col_2", "col_3"]
    assert len(dimension["memberCatalog"]["attributeColumnIds"]) == 3
    assert client.get(BASE + "/transactions").json()["items"] == []
    response = upload(
        client, b"qty,price\n10,20\n", table="NumericOnly", key="numeric_only"
    )
    assert response.status_code == 201, response.text
    assert "Choose grain keys" in response.json()["warnings"][0]
    source = next(
        s
        for s in client.get(BASE + "/sources").json()["items"]
        if s["name"] == "NumericOnly"
    )
    assert source["definition"]["inScope"]
    assert client.get(BASE + "/transactions").json()["items"] == []


def test_failed_metadata_setup_retries_without_partial_transaction(clickhouse_app):
    client, _, _, _ = clickhouse_app
    onboarding = client.app.state.container.upload_metadata
    maximum = onboarding.max_objects
    onboarding.max_objects = 1
    content = b"product,qty,value\nP1,10,20\n"
    response = upload(client, content, table="RetrySales")
    assert response.status_code == 201, response.text
    assert response.json()["semanticSetupComplete"] is False
    assert client.get(BASE + "/transactions").json()["items"] == []
    assert client.get(BASE + "/measures").json()["items"] == []
    onboarding.max_objects = maximum
    response = upload(client, content, table="RetrySales")
    assert response.status_code == 201, response.text
    assert response.json()["replayed"] is True
    assert response.json()["semanticSetupComplete"] is True
    assert len(client.get(BASE + "/transactions").json()["items"]) == 1
    assert len(client.get(BASE + "/measures").json()["items"]) == 2
