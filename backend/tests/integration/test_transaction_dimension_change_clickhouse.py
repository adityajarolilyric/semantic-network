# ruff: noqa: F811

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_incremental_clickhouse import calc, run_incremental
from .test_measures_clickhouse import (
    BASE,
    setup,
    build_values,
    values,
    measure,
    transaction,
)
from backend.tests.conftest import object_payload
from .test_planning_clickhouse import managed

pytestmark = pytest.mark.integration


def test_remapping_transaction_previews_and_rebuilds_owned_measures(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client)
    measure(client, "ForecastCopy", "tx_forecast", "base", "col_3")
    calc(client, "Double")
    assert build_values(client)["state"] == "succeeded"
    old_values, old_columns = values(client, "base")
    dimension = object_payload(client.get(BASE + "/dimensions/base").json())
    dimension.update(objectId="alternate", name="Alternate", displayName="Alternate")
    response = client.post(BASE + "/dimensions", json=dimension)
    assert response.status_code == 201, response.text
    original = client.get(BASE + "/transactions/tx_forecast").json()
    payload = object_payload(original)
    payload["definition"]["dimensionId"] = "alternate"
    measures_before = client.get(BASE + "/measures").json()
    revision_before = client.get(BASE).json()
    for flag in ("?sql_only=true", ""):
        response = client.post(
            BASE + "/transactions/resolution/preview" + flag, json=payload
        )
        assert response.status_code == 200, response.text
    assert client.get(BASE).json() == revision_before
    assert client.get(BASE + "/measures").json() == measures_before
    assert (
        client.get(BASE + "/dimensions/alternate/table").json()["state"] == "not_built"
    )
    response = client.put(
        BASE + "/transactions/tx_forecast",
        json=payload,
        headers={"If-Match": str(original["version"])},
    )
    assert response.status_code == 200, response.text
    for mid in ("FQTY", "ForecastCopy"):
        assert (
            client.get(BASE + "/measures/" + mid).json()["definition"][
                "nativeDimensionId"
            ]
            == "alternate"
        )
    assert (
        client.get(BASE + "/measures/Double").json()["definition"]["nativeDimensionId"]
        == "alternate"
    )
    assert (
        client.get(BASE + "/measures/execution/levels/base").json()["state"]
        == "needs_rebuild"
    )
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    for lid in ("base", "alternate"):
        result, columns = values(client, lid)
        for mid in ("FQTY", "ForecastCopy", "Double"):
            assert [row[columns[mid]] for row in result["rows"]] == [
                row[old_columns[mid]] for row in old_values["rows"]
            ]
    # A second build is reusable; remapping does not leave the network permanently stale.
    repeated = run_incremental(client)
    assert repeated["state"] == "succeeded", repeated
    assert repeated["reused"] is True


def test_unbound_sales_grain_price_follows_remapped_inputs(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client)
    source = managed(client, "StandaloneSales", b"product,qty,value\nP1,10,200\n")
    dimension = {
        "objectId": "sales_grain",
        "kind": "dimension",
        "name": "Sales grain",
        "displayName": "Sales grain",
        "definition": {
            "population": {"operation": "source", "sourceId": source},
            "grain": {
                "keyIds": ["sales_product"],
                "columns": [
                    {
                        "grainColumnId": "sales_product",
                        "name": "product",
                        "populationRef": {"columnId": "col_1"},
                    }
                ],
            },
            "memberBindings": [],
        },
    }
    response = client.post(BASE + "/dimensions", json=dimension)
    assert response.status_code == 201, response.text
    transaction(
        client, "local_sales", source, "sales_grain", [("sales_product", "col_1")]
    )
    measure(client, "sales_qty", "local_sales", "sales_grain", "col_2")
    measure(client, "sales_value", "local_sales", "sales_grain", "col_3")
    price = {
        "objectId": "price",
        "kind": "measure",
        "name": "price",
        "displayName": "price",
        "business": {},
        "definition": {
            "measureType": "calculated",
            "nativeDimensionId": "sales_grain",
            "calculationType": "AggregateFirst",
            "resultType": {"type": "number", "nullable": True},
            "formula": {
                "displayText": "[sales_value]/[sales_qty]",
                "languageVersion": "planning-v1",
                "astVersion": 2,
                "expression": {
                    "type": "binary",
                    "operator": "divide",
                    "left": {"type": "measureRef", "measureId": "sales_value"},
                    "right": {"type": "measureRef", "measureId": "sales_qty"},
                },
            },
        },
    }
    response = client.post(BASE + "/measures", json=price)
    assert response.status_code == 201, response.text
    assert build_values(client)["state"] == "succeeded"
    old = client.get(BASE + "/transactions/local_sales").json()
    payload = object_payload(old)
    payload["definition"].update(
        dimensionId="pt",
        keyMappings=[{"grainColumnId": "product", "sourceColumnId": "col_1"}],
    )
    before = client.get(BASE + "/measures/price").json()
    for flag in ("?sql_only=true", ""):
        response = client.post(
            BASE + "/transactions/resolution/preview" + flag, json=payload
        )
        assert response.status_code == 200, response.text
    assert client.get(BASE + "/measures/price").json() == before
    response = client.put(
        BASE + "/transactions/local_sales",
        json=payload,
        headers={"If-Match": str(old["version"])},
    )
    assert response.status_code == 200, response.text
    current = client.get(BASE + "/measures/price").json()
    assert current["definition"]["nativeDimensionId"] == "pt"
    assert current["definition"]["formula"] == before["definition"]["formula"]
    assert current["version"] == before["version"] + 1
    assert run_incremental(client)["state"] == "succeeded"
    result, columns = values(client, "pt")
    assert result["rows"][0][columns["price"]] == "20.0"
    assert "price" not in values(client, "sales_grain")[1]
