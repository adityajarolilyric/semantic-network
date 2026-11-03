# ruff: noqa: F811
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import assert_post, measure, build_values, values
from .test_transaction_resolution_clickhouse import setup_date_range

pytestmark = pytest.mark.integration


def pivot(retained, outputs, *, attr="col_2", value="col_3", keys=None):
    return {
        "stageId": "owned_pivot",
        "type": "pivot",
        "enabled": True,
        "recordKeyColumnIds": keys or [],
        "groupByColumnIds": retained,
        "pairs": [
            {
                "pairId": "attributes",
                "attributeColumnId": attr,
                "valueColumnId": value,
                "outputs": [{"attributeValue": v, "name": v} for v in outputs],
            }
        ],
    }


def schema(client, kind, oid, source, p):
    response = client.post(
        BASE + "/" + kind + "/input/schema",
        json={"objectId": oid, "sourceId": source, "pivot": p},
    )
    assert response.status_code == 200, response.text
    return response.json()["columns"]


def test_owned_dimension_pivot_preview_and_native_population_lookup(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    source = managed(
        client,
        "LongCustomers",
        b"customer_id,attribute,value\nC1,channel,Retail\nC1,sold_to,A\nC2,channel,Wholesale\nC2,sold_to,B\n",
    )
    before = client.get(BASE + "/managed-tables/" + source).json()
    p = pivot(["col_1"], ["channel", "sold_to"], keys=["col_1"])
    cols = schema(client, "dimensions", "Customer", source, p)
    other = schema(client, "dimensions", "OtherCustomer", source, p)
    assert {c["columnId"] for c in cols if c["columnId"].startswith("pv_")}.isdisjoint(
        {c["columnId"] for c in other if c["columnId"].startswith("pv_")}
    )
    customer = {
        "objectId": "Customer",
        "kind": "dimension",
        "name": "Customer",
        "displayName": "Customer",
        "isTimeDim": False,
        "definition": {
            "sourceId": source,
            "pivot": p,
            "columns": [
                {
                    "columnId": c["columnId"],
                    "name": c["name"],
                    "displayName": c["name"],
                    "sourceColumnId": c["columnId"],
                }
                for c in cols
            ],
            "keyColumnIds": ["col_1"],
            "labelColumnId": "col_1",
            "attributeColumnIds": [c["columnId"] for c in cols],
        },
    }
    response = client.post(BASE + "/dimensions/preview", json=customer)
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 2
    assert {tuple(row) for row in response.json()["rows"]} == {
        ("C1", "Retail", "A"),
        ("C2", "Wholesale", "B"),
    }
    assert_post(client, "/dimensions", customer)
    pop = managed(client, "CustomerPopulation", b"customer_id\nC1\nC2\n")
    create(
        client,
        {
            "objectId": "customer_level",
            "kind": "planningLevel",
            "name": "Customer level",
            "displayName": "Customer level",
            "definition": {
                "population": {"operation": "source", "sourceId": pop},
                "grain": {
                    "keyIds": ["customer"],
                    "columns": [
                        {
                            "grainColumnId": "customer",
                            "name": "customer_id",
                            "populationRef": {"columnId": "col_1"},
                        }
                    ],
                },
                "dimensionBindings": [
                    {
                        "dimensionId": "Customer",
                        "keyMappings": [
                            {"grainColumnId": "customer", "dimensionColumnId": "col_1"}
                        ],
                        "selectedAttributeColumnIds": [
                            c["columnId"] for c in cols if c["columnId"] != "col_1"
                        ],
                    }
                ],
            },
        },
    )
    result = build(client, "customer_level")
    assert result["state"] == "succeeded", result
    data = client.get(
        BASE + "/planning-levels/customer_level/table?preview=true"
    ).json()
    assert {tuple(row) for row in data["rows"]} == {
        ("C1", "Retail", "A"),
        ("C2", "Wholesale", "B"),
    }
    assert client.get(BASE + "/managed-tables/" + source).json() == before
    assert admin.command(f"SELECT count() FROM `{database}`.`LongCustomers`") == 4


def test_pivot_then_calendar_then_column_resolution_and_measure_load(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup_date_range(client)
    # Calendar resolution must also read an independently pivoted Time dimension.
    rows = b"date,attribute,value\n" + b"".join(
        f"2026-01-0{i},week,{'FISCAL_A' if i <= 3 else 'FISCAL_B'}\n".encode()
        for i in range(1, 8)
    )
    calendar_source = managed(client, "LongCalendar", rows)
    calendar_pivot = pivot(["col_1"], ["week"], keys=["col_1"])
    calendar_columns = schema(
        client, "dimensions", "Time", calendar_source, calendar_pivot
    )
    week_source = next(c["columnId"] for c in calendar_columns if c["name"] == "week")
    current = client.get(BASE + "/dimensions/Time").json()
    payload = {
        k: v
        for k, v in current.items()
        if k not in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
    }
    payload["definition"]["sourceId"] = calendar_source
    payload["definition"]["pivot"] = calendar_pivot
    payload["definition"]["columns"][0]["sourceColumnId"] = "col_1"
    payload["definition"]["columns"][1]["sourceColumnId"] = week_source
    response = client.put(
        BASE + "/dimensions/Time",
        json=payload,
        headers={"If-Match": str(current["version"])},
    )
    assert response.status_code == 200, response.text
    rebuilt = build(client, "period_native")
    assert rebuilt["state"] == "succeeded", rebuilt
    source = managed(
        client,
        "LongPromotions",
        b"product_attribute,product_value,start_date,end_date,quantity,sequence\nproduct,P1,2026-01-01,2026-01-07,70,10\n",
    )
    p = pivot(
        ["col_3", "col_4", "col_5", "col_6"], ["product"], attr="col_1", value="col_2"
    )
    cols = schema(client, "transactions", "promotion", source, p)
    product = next(c["columnId"] for c in cols if c["name"] == "product")
    response = client.post(
        BASE + "/transactions/input/preview",
        json={"objectId": "promotion", "sourceId": source, "pivot": p},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 1
    cfg = {
        "dimensionId": "Time",
        "startColumnId": "col_3",
        "endColumnId": "col_4",
        "selectedColumnIds": ["week"],
    }
    response = client.post(
        BASE + "/transactions/calendar/schema",
        json={
            "transactionId": "promotion",
            "sourceId": source,
            "pivot": p,
            "calendarResolution": cfg,
        },
    )
    assert response.status_code == 200, response.text
    week = next(
        c["columnId"]
        for c in response.json()["columns"]
        if c.get("dimensionColumnId") == "week"
    )
    tx = {
        "objectId": "promotion",
        "kind": "transaction",
        "name": "Promotion",
        "displayName": "Promotion",
        "definition": {
            "sourceId": source,
            "pivot": p,
            "calendarResolution": cfg,
            "planningLevelId": "period_native",
            "mappingMode": "resolve_multi_grain",
            "resolution": {
                "axes": [
                    {
                        "grainColumnId": grain,
                        "rules": [
                            {
                                "ruleId": "match_" + grain,
                                "sourceColumnId": cid,
                                "target": {"columnId": grain},
                            }
                        ],
                    }
                    for grain, cid in [("product", product), ("period", week)]
                ],
                "values": [
                    {"sourceColumnId": "col_5", "mode": "copy", "timeMode": "split"}
                ],
                "orderColumnIds": ["col_6"],
            },
        },
    }
    response = client.post(BASE + "/transactions/resolution/preview", json=tx)
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 7
    assert_post(client, "/transactions", tx)
    measure(client, "PromoQty", "promotion", "period_native", "col_5")
    result = build_values(client)
    assert result["state"] == "succeeded", result
    data, indices = values(client, "period_native")
    actual = [r[indices["PromoQty"]] for r in data["rows"]]
    assert actual.count("30.0") == 1 and actual.count("40.0") == 1
    current = client.get(BASE + "/transactions/promotion").json()
    assert current["definition"]["pivot"] == {
        **p,
        "pairs": [
            {
                **p["pairs"][0],
                "outputs": [
                    {"attributeValue": "product", "name": "product", "columnId": None}
                ],
            }
        ],
    }


def test_owned_pivot_duplicate_cells_fail_without_modifying_shared_managed_table(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    source = managed(
        client,
        "ConflictingAttributes",
        b"id,attribute,value\n1,channel,A\n1,channel,B\n",
    )
    before = client.get(BASE + "/managed-tables/" + source).json()
    p = pivot(["col_1"], ["channel"], keys=["col_1"])
    response = client.post(
        BASE + "/dimensions/input/preview",
        json={"objectId": "dimension", "sourceId": source, "pivot": p},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "duplicate_pivot_cell"
    assert client.get(BASE + "/managed-tables/" + source).json() == before


def test_direct_pivot_value_columns_are_available_to_measures(clickhouse_app):
    client, _, _, _ = clickhouse_app
    pop = managed(client, "ProductPopulation", b"product\nP1\nP2\n")
    create(
        client,
        {
            "objectId": "products",
            "kind": "planningLevel",
            "name": "Products",
            "displayName": "Products",
            "definition": {
                "population": {"operation": "source", "sourceId": pop},
                "grain": {
                    "keyIds": ["product"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"columnId": "col_1"},
                        }
                    ],
                },
                "dimensionBindings": [],
            },
        },
    )
    rev = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"basePlanningLevelId": "products", "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert response.status_code == 200, response.text
    assert build(client, "products")["state"] == "succeeded"
    source = managed(
        client,
        "LongQuantities",
        b"product,attribute,value\nP1,Qty,10\nP1,Other,5\nP2,Qty,20\n",
    )
    p = pivot(["col_1"], ["Qty", "Other"], keys=["col_1"])
    cols = schema(client, "transactions", "quantities", source, p)
    qty = next(c["columnId"] for c in cols if c["name"] == "Qty")
    tx = {
        "objectId": "quantities",
        "kind": "transaction",
        "name": "Quantities",
        "displayName": "Quantities",
        "definition": {
            "sourceId": source,
            "planningLevelId": "products",
            "pivot": p,
            "keyMappings": [{"sourceColumnId": "col_1", "grainColumnId": "product"}],
        },
    }
    assert_post(client, "/transactions", tx)
    response = client.get(BASE + "/transactions/quantities/schema")
    assert response.status_code == 200, response.text
    assert any(
        c["columnId"] == qty and c["name"] == "Qty" for c in response.json()["columns"]
    )
    response = client.post(BASE + "/transactions/resolution/preview", json=tx)
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 2
    measure(client, "FQTY", "quantities", "products", qty)
    result = build_values(client)
    assert result["state"] == "succeeded", result
    data, index = values(client, "products")
    assert [r[index["FQTY"]] for r in data["rows"]] == ["10.0", "20.0"]
