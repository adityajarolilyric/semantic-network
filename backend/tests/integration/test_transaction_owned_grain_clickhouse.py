# ruff: noqa: F811
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed
from backend.tests.conftest import object_payload
from .test_managed_clickhouse import await_run
from .test_incremental_clickhouse import run_incremental
from .test_measures_clickhouse import build_values, values

pytestmark = pytest.mark.integration


def test_transaction_materialises_without_dimensions_or_base(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    source = managed(
        client,
        "SalesStandalone",
        b"product,location,qty\nP1,L1,10\nP1,L1,20\nP2,L1,5\n",
    )
    response = client.post(
        BASE + "/transactions",
        json={
            "objectId": "sales",
            "name": "Sales",
            "displayName": "Sales",
            "kind": "transaction",
            "definition": {"sourceId": source, "grainColumnIds": ["col_1", "col_2"]},
            "measures": [
                {
                    "valueColumnId": "col_3",
                    "aggregation": {"nonTime": "sum", "time": "sum"},
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    transaction = response.json()
    assert transaction["definition"]["dimensionId"] is None
    assert client.get(BASE + "/dimensions").json()["items"] == []
    assert client.get(BASE).json()["baseDimensionId"] is None
    measures = client.get(BASE + "/measures").json()["items"]
    assert len(measures) == 1
    assert "nativeGrainId" not in measures[0]["definition"]
    assert "nativeDimensionId" not in measures[0]["definition"]
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, columns = values(client, transaction["effectiveGrainId"])
    assert sorted(row[columns[measures[0]["name"]]] for row in result["rows"]) == [
        "30.0",
        "5.0",
    ]
    assert (
        admin.command(
            f"SELECT count() FROM system.tables WHERE database='{database}' AND startsWith(comment,'semanticTable:')"
        )
        == 1
    )

    mid = measures[0]["objectId"]
    options = client.get(BASE + "/decision-view/options").json()
    assert options["dimensions"] == []
    assert options["transactions"][0]["transactionId"] == "sales"
    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"transactionId": "sales", "columnId": "col_1"}],
            "measures": [mid],
        },
    )
    assert response.status_code == 200, response.text
    assert sorted(row[-1] for row in response.json()["rows"]) == ["30.0", "5.0"]


def add_transaction(client, oid, source, value_columns):
    response = client.post(
        BASE + "/transactions",
        json={
            "objectId": oid,
            "kind": "transaction",
            "name": oid.title(),
            "displayName": oid.title(),
            "definition": {"sourceId": source, "grainColumnIds": ["col_1", "col_2"]},
            "measures": [
                {
                    "valueColumnId": column,
                    "editable": True,
                    "aggregation": {"nonTime": "sum", "time": "sum"},
                }
                for column in value_columns
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_calculation(client, mid, transaction, expression):
    return client.post(
        BASE + "/measures",
        json={
            "objectId": mid,
            "kind": "measure",
            "name": mid,
            "displayName": mid,
            "business": {},
            "definition": {
                "measureType": "calculated",
                "outputGrain": {"kind": "transaction", "objectId": transaction},
                "calculationType": "AggregateFirst",
                "resultType": {"type": "number", "nullable": True},
                "formula": {"displayText": mid, "expression": expression},
            },
        },
    )


def query(client, transaction, mids):
    request = {
        "rows": [{"transactionId": transaction, "columnId": "col_1"}],
        "columns": [],
        "measures": mids,
    }
    response = client.post(BASE + "/decision-view/query", json=request)
    return response, request


def test_independent_transactions_edits_formulas_then_explicit_binding(clickhouse_app):
    client, _, _, _ = clickhouse_app
    sales_source = managed(
        client,
        "SalesFacts",
        b"product,location,sales_qty,sales_value\nP1,L1,10,200\nP1,L1,20,400\nP1,L2,10,300\nP2,L1,5,150\n",
    )
    forecast_source = managed(
        client, "ForecastFacts", b"product,location,forecast_qty\nP1,L1,50\nP3,L1,7\n"
    )
    sales = add_transaction(client, "sales", sales_source, ["col_3", "col_4"])
    forecast = add_transaction(client, "forecast", forecast_source, ["col_3"])
    measures = {m["name"]: m for m in client.get(BASE + "/measures").json()["items"]}
    qty, value, fqty = (
        measures[name]["objectId"]
        for name in ("sales_qty", "sales_value", "forecast_qty")
    )
    expression = {
        "type": "binary",
        "operator": "divide",
        "left": {"type": "measureRef", "measureId": value},
        "right": {"type": "measureRef", "measureId": qty},
    }
    response = add_calculation(client, "price", "sales", expression)
    assert response.status_code == 201, response.text
    response = add_calculation(
        client,
        "intersections",
        "sales",
        {"type": "count", "function": "count_rows", "arguments": []},
    )
    assert response.status_code == 201, response.text
    revision = client.get(BASE).json()
    rejected = add_calculation(
        client,
        "unconnected",
        "sales",
        {
            "type": "binary",
            "operator": "subtract",
            "left": {"type": "measureRef", "measureId": fqty},
            "right": {"type": "measureRef", "measureId": qty},
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert client.get(BASE).json() == revision
    assert build_values(client)["state"] == "succeeded"
    response, view = query(client, "sales", [qty, "price", "intersections"])
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [
        ["P1", "40.0", "22.5", "2.0"],
        ["P2", "5.0", "30.0", "1.0"],
    ]
    rejected, _ = query(client, "sales", [qty, fqty])
    assert rejected.status_code == 422, rejected.text
    edit = client.post(
        BASE + "/decision-view/edits",
        json={
            "view": view,
            "measureId": qty,
            "memberValues": ["P1"],
            "value": 80,
            **response.json()["editContext"],
        },
        headers={"Idempotency-Key": "standalone-edit"},
    )
    edited = await_run(client, BASE, edit)
    assert edited["state"] == "succeeded", edited
    response, _ = query(client, "sales", [qty])
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0] == ["P1", "80.0"]
    response, _ = query(client, "forecast", [fqty])
    assert response.json()["rows"] == [["P1", "50.0"], ["P3", "7.0"]]
    population = managed(
        client, "SharedIntersections", b"product,location\nP1,L1\nP1,L2\nP2,L1\nP3,L1\n"
    )
    response = client.post(
        BASE + "/dimensions",
        json={
            "objectId": "common",
            "kind": "dimension",
            "name": "ProductLocation",
            "displayName": "ProductLocation",
            "definition": {
                "population": {"operation": "source", "sourceId": population},
                "grain": {
                    "keyIds": ["p", "l"],
                    "columns": [
                        {
                            "grainColumnId": key,
                            "name": name,
                            "populationRef": {"columnId": col},
                        }
                        for key, name, col in [
                            ("p", "product", "col_1"),
                            ("l", "location", "col_2"),
                        ]
                    ],
                },
                "memberBindings": [],
            },
        },
    )
    assert response.status_code == 201, response.text
    price = client.get(BASE + "/measures/price").json()
    for transaction in (sales, forecast):
        current = client.get(BASE + "/transactions/" + transaction["objectId"]).json()
        payload = object_payload(current)
        payload["definition"].update(
            dimensionId="common",
            keyMappings=[
                {"sourceColumnId": "col_1", "grainColumnId": "p"},
                {"sourceColumnId": "col_2", "grainColumnId": "l"},
            ],
        )
        saved = client.put(
            BASE + "/transactions/" + transaction["objectId"],
            json=payload,
            headers={"If-Match": str(current["version"])},
        )
        assert saved.status_code == 200, saved.text
    assert (
        client.get(BASE + "/measures/price").json()["definition"] == price["definition"]
    )
    assert client.get(BASE).json()["baseDimensionId"] is None
    built = run_incremental(client)
    assert built["state"] == "succeeded", built
    combined, _ = query(client, "sales", [qty, fqty, "price"])
    assert combined.status_code == 200, combined.text
    assert combined.json()["rows"] == [
        ["P1", "40.0", "50.0", "22.5"],
        ["P2", "5.0", None, "30.0"],
        ["P3", None, "7.0", None],
    ]
    assert client.get(BASE + "/measures/price").json()["definition"]["outputGrain"] == {
        "kind": "transaction",
        "objectId": "sales",
    }
