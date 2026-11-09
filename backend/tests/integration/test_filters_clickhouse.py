# ruff: noqa: F811
from copy import deepcopy
import pytest
from .test_csv_clickhouse import authored_clickhouse_app, clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build, table, level
from .test_measures_clickhouse import assert_post, measure, build_values, values
from .test_transaction_resolution_clickhouse import setup_resolution
from .test_object_pivots_clickhouse import pivot, schema
from backend.tests.test_filters import filters, rule
from backend.app.engine.transformations.filters import FilterCompiler

pytestmark = pytest.mark.integration


def test_typed_predicates_in_clickhouse(authored_clickhouse_app):
    _, admin, _, _ = authored_clickhouse_app
    source = "SELECT 'Promo_20%' AS name, toFloat64(12.5) AS qty, toBool(1) AS active, toDate('2026-01-02') AS day, toDateTime64('2026-01-02 10:30:00', 3, 'UTC') AS stamp, CAST(NULL AS Nullable(String)) AS empty"
    columns = {
        "name": "String",
        "qty": "Float64",
        "active": "Bool",
        "day": "Date",
        "stamp": "DateTime64(3, 'UTC')",
        "empty": "Nullable(String)",
    }
    definitions = [
        filters(rule("name", "contains", "_20%")),
        filters(rule("name", "not_contains", "sale")),
        filters(rule("name", "starts_with", "Promo")),
        filters(rule("name", "ends_with", "%")),
        filters(rule("qty", "between", "10", "13")),
        filters(rule("qty", "not_between", "0", "5")),
        filters(rule("active", "eq", True)),
        filters(rule("day", "gte", "2026-01-01")),
        filters(rule("stamp", "eq", "2026-01-02T10:30:00")),
        filters(rule("stamp", "eq", "2026-01-02T10:30:00+00:00")),
        filters(rule("empty", "is_null")),
    ]
    for definition in definitions:
        sql = FilterCompiler().apply(source, definition, columns)
        assert len(admin.query(sql).result_rows) == 1, sql
    assert (
        admin.query(
            FilterCompiler().apply(
                source, filters(rule("empty", "not_in", "x")), columns
            )
        ).result_rows
        == []
    )


def test_dimension_filters_after_owned_pivot_and_lookup(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    sid = managed(
        client,
        "LongCustomerFilter",
        b"customer,attribute,value\nC1,channel,Retail\nC2,channel,Wholesale\n",
    )
    p = pivot(["col_1"], ["channel"], keys=["col_1"])
    cols = schema(client, "dimension-members", "Customer", sid, p)
    channel = next(c["columnId"] for c in cols if c["name"] == "channel")
    dim = {
        "objectId": "Customer",
        "name": "Customer",
        "displayName": "Customer",
        "kind": "dimensionMembers",
        "isTimeDim": False,
        "definition": {
            "sourceId": sid,
            "pivot": p,
            "columns": [
                {
                    "columnId": c["columnId"],
                    "sourceColumnId": c["columnId"],
                    "name": c["name"],
                    "displayName": c["name"],
                }
                for c in cols
            ],
            "keyColumnIds": ["col_1"],
            "labelColumnId": "col_1",
            "attributeColumnIds": [c["columnId"] for c in cols],
            "filters": filters(rule(channel, "contains", "Retail")),
        },
    }
    response = client.post(BASE + "/dimension-members/preview", json=dim)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [["C1", "Retail"]]
    assert_post(client, "/dimension-members", dim)
    pop = managed(client, "PopulationFilter", b"customer\nC1\n")
    pl = level(pop, attribute=False)
    pl["definition"]["memberBindings"] = [
        {
            "memberCatalogId": "Customer",
            "keyMappings": [{"grainColumnId": "product", "memberColumnId": "col_1"}],
            "selectedAttributeColumnIds": [channel],
        }
    ]
    create(client, pl)
    assert build(client, "pl_main")["state"] == "succeeded"
    assert table(client, "pl_main")["rows"] == [["C1", "Retail"]]


def test_planning_filters_for_source_cartesian_and_projection(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    sid = managed(client, "FilterPopulation", b"product,category\nP1,Keep\nP2,Drop\n")
    base = level(sid)
    response = client.post(BASE + "/dimensions/schema", json=base)
    assert response.status_code == 200, response.text
    assert {c["columnId"] for c in response.json()["columns"]} == {
        "product",
        "category",
    }
    create(client, base)
    assert build(client, "pl_main")["state"] == "succeeded"
    child = level(sid, oid="child")
    child["definition"]["population"] = {
        "operation": "distinct_projection",
        "sourceDimensionId": "pl_main",
    }
    child["definition"]["grain"]["columns"][0]["populationRef"] = {
        "sourceGrainColumnId": "product"
    }
    child["definition"]["populationAttributes"][0]["populationRef"] = {
        "sourceOutputColumnId": "category"
    }
    child["definition"]["filters"] = filters(rule("category", "eq", "Keep"))
    create(client, child)
    assert build(client, "child")["state"] == "succeeded"
    assert table(client, "child")["rows"] == [["P1", "Keep"]]
    time = managed(client, "FilterTime", b"week\nW1\nW2\n")
    cart = level(sid, oid="cart")
    cart["definition"]["population"] = {
        "operation": "cartesian_product",
        "factors": [
            {"factorId": "p", "sourceId": sid, "selectedColumnIds": ["col_1", "col_2"]},
            {"factorId": "t", "sourceId": time, "selectedColumnIds": ["col_1"]},
        ],
    }
    cart["definition"]["grain"]["columns"][0]["populationRef"]["factorId"] = "p"
    cart["definition"]["grain"]["columns"].append(
        {
            "grainColumnId": "week",
            "name": "week",
            "populationRef": {"factorId": "t", "columnId": "col_1"},
        }
    )
    cart["definition"]["grain"]["keyIds"].append("week")
    cart["definition"]["populationAttributes"][0]["populationRef"]["factorId"] = "p"
    cart["definition"]["filters"] = filters(
        rule("category", "eq", "Keep"), rule("week", "eq", "W2")
    )
    create(client, cart)
    assert build(client, "cart")["state"] == "succeeded"
    assert table(client, "cart")["rows"] == [["P1", "W2", "Keep"]]


def test_direct_transaction_filter_before_measure_aggregation(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    pop = managed(client, "DirectPopulation", b"product\nP1\nP2\n")
    create(client, level(pop, attribute=False))
    assert build(client, "pl_main")["state"] == "succeeded"
    revision = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"baseDimensionId": "pl_main", "changeReason": ""},
        headers={"If-Match": str(revision["version"])},
    )
    assert response.status_code == 200, response.text
    sid = managed(
        client,
        "DirectOrders",
        b"product,qty,valid,date\nP1,10,true,2026-01-01\nP1,90,false,2026-01-02\nP2,30,true,2026-02-01\n",
    )
    tx = {
        "objectId": "Orders",
        "kind": "transaction",
        "name": "Orders",
        "displayName": "Orders",
        "definition": {
            "sourceId": sid,
            "dimensionId": "pl_main",
            "keyMappings": [{"sourceColumnId": "col_1", "grainColumnId": "product"}],
            "filters": filters(
                rule("col_3", "eq", True),
                rule("col_4", "between", "2026-01-01", "2026-01-31"),
            ),
        },
    }
    assert_post(client, "/transactions", tx)
    response = client.post(BASE + "/transactions/resolution/preview", json=tx)
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 1
    assert response.json()["diagnostics"]["filteredOutRows"] == 2
    measure(client, "Qty", "Orders", "pl_main", "col_2")
    assert build_values(client)["state"] == "succeeded"
    assert values(client, "pl_main")[0]["rows"] == [["P1", "10.0"], ["P2", None]]
    bad = deepcopy(tx)
    bad["objectId"] = "Bad"
    bad["definition"]["filters"] = filters(rule("col_2", "contains", "1"))
    assert client.post(BASE + "/transactions", json=bad).status_code == 422


def test_multi_grain_filters_after_last_wins_without_resurrecting_old_assignment(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(client)
    sid = managed(
        client,
        "WideAssignments",
        b"product,location,qty,sequence,status\nP1,L1,100,1,keep\nP1,L1,30,2,drop\n",
    )
    tx["definition"]["sourceId"] = sid
    tx["definition"]["resolution"] = {
        "axes": [
            {
                "grainColumnId": k,
                "rules": [
                    {
                        "ruleId": k,
                        "kind": "column",
                        "sourceColumnId": cid,
                        "target": {"columnId": k},
                    }
                ],
            }
            for k, cid in [("product", "col_1"), ("location", "col_2")]
        ],
        "values": [{"sourceColumnId": "col_3", "mode": "copy"}],
        "orderColumnIds": ["col_4"],
        "passthroughColumnIds": ["col_5"],
    }
    tx["definition"]["filters"] = filters(rule("col_5", "eq", "keep"))
    schema_response = client.post(BASE + "/transactions/resolution/schema", json=tx)
    assert schema_response.status_code == 200, schema_response.text
    assert all(c["physicalType"] for c in schema_response.json()["columns"])
    response = client.post(BASE + "/transactions/resolution/preview", json=tx)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == []
    assert response.json()["diagnostics"]["filteredOutRows"] == 1
    assert (
        response.json()["diagnostics"]["overwrittenMatches"]
        + response.json()["diagnostics"]["shadowedAssignments"]
        == 1
    )
    tx["definition"]["filters"] = filters(rule("col_5", "eq", "drop"))
    assert_post(client, "/transactions", tx)
    response = client.post(BASE + "/transactions/resolution/preview", json=tx)
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 1
    measure(client, "Qty", tx["objectId"], "native", "col_3")
    assert build_values(client)["state"] == "succeeded"
    result = values(client, "native")[0]
    assert [r[-1] for r in result["rows"]] == ["30.0", None, None]
