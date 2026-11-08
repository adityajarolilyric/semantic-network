# ruff: noqa: F811
import pytest

from .test_csv_clickhouse import authored_clickhouse_app, clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed
from .test_measures_clickhouse import build_values, values

pytestmark = pytest.mark.integration


def test_transaction_population_preview_reuse_and_network(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    forecast_source = managed(
        client,
        "ForecastPopulation",
        b"product,location,week,forecast_qty\nP1,L1,W1,10\nP1,L1,W1,20\nP2,L1,W1,5\n",
    )
    sales_source = managed(
        client,
        "SalesPopulation",
        b"product,location,week,sales_qty\nP1,L1,W1,8\nP2,L1,W1,4\n",
    )
    keys = ["col_1", "col_2", "col_3"]
    population = {
        "objectId": "shared_plt",
        "kind": "dimension",
        "name": "Active PLT",
        "displayName": "Active PLT",
        "definition": {
            "population": {
                "operation": "source",
                "sourceId": forecast_source,
                "inputTransform": {"objectId": "forecast", "filters": None},
            },
            "grain": {
                "keyIds": keys,
                "columns": [
                    {
                        "grainColumnId": key,
                        "name": name,
                        "populationRef": {"columnId": key},
                    }
                    for key, name in zip(keys, ["product", "location", "week"])
                ],
            },
            "memberBindings": [],
        },
    }

    def command(transaction_id, source, population_definition=None):
        return {
            "transaction": {
                "objectId": transaction_id,
                "kind": "transaction",
                "name": transaction_id,
                "displayName": transaction_id,
                "definition": {
                    "sourceId": source,
                    "dimensionId": "shared_plt",
                    "grainColumnIds": keys,
                    "keyMappings": [
                        {"grainColumnId": key, "sourceColumnId": key} for key in keys
                    ],
                },
                "measures": [
                    {
                        "valueColumnId": "col_4",
                        "aggregation": {"nonTime": "sum", "time": "sum"},
                    }
                ],
            },
            "population": population_definition,
            "expectedRevisionVersion": client.get(BASE).json()["version"],
            "useAsBase": population_definition is not None,
        }

    draft = command("forecast", forecast_source, population)
    preview = client.post(BASE + "/transactions/population/preview", json=draft)
    assert preview.status_code == 200, preview.text
    assert preview.json()["rowCount"] == 3
    assert client.get(BASE + "/dimensions").json()["items"] == []
    sql = client.post(
        BASE + "/transactions/population/preview?sql_only=true", json=draft
    )
    assert sql.status_code == 200, sql.text
    assert sql.json()["sql"]
    response = client.post(BASE + "/transactions/population/save", json=draft)
    assert response.status_code == 200, response.text
    response = client.post(
        BASE + "/transactions/population/save", json=command("sales", sales_source)
    )
    assert response.status_code == 200, response.text
    assert len(client.get(BASE + "/dimensions").json()["items"]) == 1
    assert client.get(BASE).json()["baseDimensionId"] == "shared_plt"
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, columns = values(client, "shared_plt")
    quantities = sorted(
        (float(row[columns["forecast_qty"]]), float(row[columns["sales_qty"]]))
        for row in result["rows"]
    )
    assert quantities == [(5, 4), (30, 8)]
