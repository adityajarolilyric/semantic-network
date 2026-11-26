# ruff: noqa: F811
from copy import deepcopy
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, build, table
from .test_measures_clickhouse import (
    assert_post,
    transaction,
    measure,
    build_values,
    update_measure,
)
from .test_managed_clickhouse import await_run
from .test_transaction_grains_clickhouse import onboarding, post

pytestmark = pytest.mark.integration


def source_dimension(client, sid, *, filters=None):
    return assert_post(
        client,
        "/dimension-members",
        {
            "objectId": "Product",
            "name": "Product",
            "displayName": "Product",
            "isTimeDim": False,
            "withGrain": True,
            "definition": {
                "sourceId": sid,
                "columns": [
                    {
                        "columnId": "pid",
                        "name": "product_id",
                        "displayName": "Product ID",
                        "sourceColumnId": "col_1",
                    },
                    {
                        "columnId": "brand",
                        "name": "brand",
                        "displayName": "Brand",
                        "sourceColumnId": "col_2",
                    },
                ],
                "keyColumnIds": ["pid"],
                "labelColumnId": "pid",
                "attributeColumnIds": ["pid", "brand"],
                "filters": filters,
            },
        },
    )


def test_product_and_compound_dimensions_share_measure_math(clickhouse_app):
    client, _, _, _ = clickhouse_app
    products = managed(client, "ProductMaster", b"product_id,brand\nP1,A\nP2,B\n")
    source_dimension(client, products)
    grain = client.post(BASE + "/dimension-members/Product/grain").json()
    product_id = grain["objectId"]
    version = client.get(BASE).json()["version"]
    assert client.post(BASE + "/dimension-members/Product/grain").json() == grain
    assert client.get(BASE).json()["version"] == version  # adoption is idempotent
    assert grain["definition"]["grain"]["keyIds"] == ["pid"]
    assert (
        client.get(BASE + "/dimensions/" + product_id + "/table").json()["state"]
        == "not_built"
    )
    documents = client.portal.call(
        lambda: client.app.state.container.store.repository.list(
            "semantic_objects", {}, key="objectId", limit=100
        )
    )
    assert not any(document["kind"] == "dimensionMembers" for document in documents)
    assert (
        next(document for document in documents if document["objectId"] == product_id)[
            "memberCatalogId"
        ]
        == "Product"
    )
    targets = managed(
        client, "ProductTargets", b"product_id,target_qty\nP1,120\nP2,60\n"
    )
    transaction(client, "Targets", targets, product_id, [("pid", "col_1")])
    measure(
        client, "TargetQty", "Targets", product_id, "col_2", mode="weighted_by_self"
    )
    update_measure(
        client, "TargetQty", lambda definition: definition.update(editable=True)
    )
    forecast = managed(
        client,
        "ForecastPLT",
        b"product_id,location_id,week_id,forecast_qty\nP1,L1,W1,40\nP1,L2,W1,60\nP2,L1,W1,50\n",
    )
    payload = onboarding(forecast)
    payload["dimensionName"] = "ProductLocationTime"
    created = post(client, payload)
    compound = created["dimension"]
    cid = compound["objectId"]
    columns = compound["definition"]["grain"]["columns"]
    pid = columns[0]["grainColumnId"]
    # Explicit relationship: matching names do not prove a Product rollup.
    edited = {
        k: deepcopy(v)
        for k, v in compound.items()
        if k not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    edited["definition"]["memberBindings"] = [
        {
            "memberCatalogId": "Product",
            "keyMappings": [{"grainColumnId": pid, "memberColumnId": "pid"}],
            "selectedAttributeColumnIds": ["brand"],
        }
    ]
    response = client.put(
        BASE + "/dimensions/" + cid,
        json=edited,
        headers={"If-Match": str(compound["version"])},
    )
    assert response.status_code == 200, response.text
    rev = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"baseDimensionId": cid, "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert response.status_code == 200, response.text
    assert (
        client.get(BASE + "/dimensions/" + cid + "/table").json()["state"]
        == "not_built"
    )
    measure(client, "FQTY", "Forecast", cid, "col_4")
    sales = managed(
        client,
        "SalesPLT",
        b"product_id,location_id,week_id,sales_qty\nP1,L1,W1,30\nP1,L2,W1,50\nP2,L1,W1,40\n",
    )
    transaction(
        client,
        "Sales",
        sales,
        cid,
        [(c["grainColumnId"], f"col_{i}") for i, c in enumerate(columns, 1)],
    )
    measure(client, "SQTY", "Sales", cid, "col_4")
    formula = assert_post(
        client,
        "/measures",
        {
            "business": {},
            "objectId": "ErrorQty",
            "name": "ErrorQty",
            "displayName": "ErrorQty",
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": cid,
                "calculationType": "AggregateFirst",
                "resultType": {"type": "number", "nullable": True},
                "formula": {
                    "displayText": "ABS([FQTY]-[SQTY])",
                    "expression": {
                        "type": "call",
                        "function": "abs",
                        "arguments": [
                            {
                                "type": "binary",
                                "operator": "subtract",
                                "left": {"type": "measureRef", "measureId": "FQTY"},
                                "right": {"type": "measureRef", "measureId": "SQTY"},
                            }
                        ],
                    },
                },
            },
        },
    )
    assert formula["definition"]["nativeDimensionId"] == cid
    result = build_values(client)
    assert result["state"] == "succeeded", result
    view = {
        "rows": [{"memberCatalogId": "Product", "columnId": "pid"}],
        "columns": [],
        "measures": ["FQTY", "SQTY", "ErrorQty", "TargetQty"],
    }
    response = client.post(BASE + "/decision-view/query", json=view)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [
        ["P1", "100.0", "80.0", "20.0", "120.0"],
        ["P2", "50.0", "40.0", "10.0", "60.0"],
    ]
    # Single public Product input in formulas: hidden engine grain must not duplicate it.
    response = client.post(
        BASE + "/measures/formula/validate",
        json={
            "objectId": "BrandFormula",
            "name": "BrandFormula",
            "nativeDimensionId": cid,
            "calculationType": "AggregateFirst",
            "displayText": "[Product.Brand]",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"], response.text
    options = client.get(BASE + "/decision-view/options").json()
    assert any(level["dimensionId"] == product_id for level in options["dimensions"])
    assert options["dimensionMembers"] == []
    base_view = {
        "rows": [{"dimensionId": cid, "columnId": c["grainColumnId"]} for c in columns],
        "columns": [],
        "measures": ["TargetQty"],
    }
    distributed = client.post(BASE + "/decision-view/query", json=base_view)
    assert distributed.status_code == 200, distributed.text
    assert distributed.json()["rows"] == [
        ["P1", "L1", "W1", "60.0"],
        ["P1", "L2", "W1", "60.0"],
        ["P2", "L1", "W1", "60.0"],
    ]
    current = client.post(BASE + "/decision-view/query", json=view).json()
    edit = client.post(
        BASE + "/decision-view/edits",
        json={
            "view": view,
            "measureId": "TargetQty",
            "memberValues": ["P1"],
            "value": 180,
            "generationId": current["editContext"]["generationId"],
            "revisionVersion": current["editContext"]["revisionVersion"],
        },
        headers={"Idempotency-Key": "unified_dimension_edit"},
    )
    edited_run = await_run(client, BASE, edit)
    assert edited_run["state"] == "succeeded", edited_run
    assert client.post(BASE + "/decision-view/query", json=base_view).json()[
        "rows"
    ] == [
        ["P1", "L1", "W1", "90.0"],
        ["P1", "L2", "W1", "90.0"],
        ["P2", "L1", "W1", "60.0"],
    ]
    # Editing source DimensionMembers metadata updates its internal grain atomically.
    old = client.get(BASE + "/dimension-members/Product").json()
    edited = {
        k: v
        for k, v in old.items()
        if k not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    edited["description"] = "Product master updated"
    response = client.put(
        BASE + "/dimension-members/Product",
        json=edited,
        headers={"If-Match": str(old["version"])},
    )
    assert response.status_code == 200, response.text
    updated = client.post(BASE + "/dimension-members/Product/grain").json()
    assert updated["version"] == grain["version"] + 1
    assert updated["definition"]["grain"]["keyIds"] == ["pid"]


def test_filtered_dimension_grain_uses_only_filtered_members(clickhouse_app):
    client, _, _, _ = clickhouse_app
    sid = managed(client, "FilteredProducts", b"product_id,brand\nP1,A\nP2,B\n")
    source_dimension(
        client,
        sid,
        filters={
            "root": {
                "type": "group",
                "combinator": "and",
                "children": [
                    {
                        "type": "condition",
                        "columnId": "brand",
                        "operator": "eq",
                        "values": ["A"],
                    }
                ],
            }
        },
    )
    grain = client.post(BASE + "/dimension-members/Product/grain").json()
    result = build(client, grain["objectId"])
    assert result["state"] == "succeeded", result
    assert table(client, grain["objectId"])["rows"] == [["P1", "A"]]


def test_unbuilt_parent_projection_preview_then_one_network_build(
    clickhouse_app, monkeypatch
):
    client, admin, _, _ = clickhouse_app
    source = managed(
        client,
        "DailyIntersections",
        b"product_id,location_id,date,week\nP1,L1,2026-01-01,W1\nP1,L1,2026-01-02,W1\nP2,L1,2026-01-01,W1\n",
    )
    daily = {
        "objectId": "Daily",
        "kind": "dimension",
        "name": "Daily",
        "displayName": "Daily",
        "definition": {
            "population": {"operation": "source", "sourceId": source},
            "grain": {
                "keyIds": ["p", "l", "d"],
                "columns": [
                    {
                        "grainColumnId": cid,
                        "name": name,
                        "populationRef": {"columnId": f"col_{index}"},
                    }
                    for index, (cid, name) in enumerate(
                        [("p", "product_id"), ("l", "location_id"), ("d", "date")], 1
                    )
                ],
            },
            "populationAttributes": [
                {
                    "columnId": "week",
                    "name": "week",
                    "populationRef": {"columnId": "col_4"},
                }
            ],
            "memberBindings": [],
        },
    }
    response = client.post(BASE + "/dimensions", json=daily)
    assert response.status_code == 201, response.text
    weekly = {
        "objectId": "Weekly",
        "kind": "dimension",
        "name": "Weekly",
        "displayName": "Weekly",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourceDimensionId": "Daily",
            },
            "grain": {
                "keyIds": ["p", "l", "w"],
                "columns": [
                    {"grainColumnId": cid, "name": name, "populationRef": {refkey: ref}}
                    for cid, name, refkey, ref in [
                        ("p", "product_id", "sourceGrainColumnId", "p"),
                        ("l", "location_id", "sourceGrainColumnId", "l"),
                        ("w", "week", "sourceOutputColumnId", "week"),
                    ]
                ],
            },
            "memberBindings": [],
        },
    }
    executor = client.app.state.container.dimension_builds.executor

    async def forbidden_execution(*args, **kwargs):
        raise AssertionError("SQL viewing must not execute the final query")

    with monkeypatch.context() as patch:
        patch.setattr(executor, "preview", forbidden_execution)
        patch.setattr(executor, "estimate", forbidden_execution)
        patch.setattr(executor, "checks", forbidden_execution)
        sql_response = client.post(
            BASE + "/dimensions/preview/population?sql_only=true", json=weekly
        )
    assert sql_response.status_code == 200, sql_response.text
    assert sql_response.json()["dialect"] == "clickhouse"
    assert (
        admin.command("SELECT count() FROM (" + sql_response.json()["sql"] + ")") == 2
    )
    response = client.post(BASE + "/dimensions/preview/population", json=weekly)
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 2
    response = client.post(BASE + "/dimensions", json=weekly)
    assert response.status_code == 201, response.text
    for oid in ("Daily", "Weekly"):
        assert client.get(BASE + "/dimensions/" + oid + "/table").json()["state"] in {
            "not_built",
            "needs_rebuild",
        }
    forecast = managed(
        client,
        "WeeklyForecast",
        b"product_id,location_id,week,qty\nP1,L1,W1,100\nP2,L1,W1,60\n",
    )
    tx = transaction(
        client,
        "Forecast",
        forecast,
        "Weekly",
        [("p", "col_1"), ("l", "col_2"), ("w", "col_3")],
    )
    preview_payload = {
        k: tx[k] for k in ("objectId", "kind", "name", "displayName", "definition")
    }
    response = client.post(
        BASE + "/transactions/resolution/preview", json=preview_payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 2
    assert client.get(BASE + "/dimensions/Weekly/table").json()["state"] != "ready"
    measure(client, "FQTY", "Forecast", "Weekly", "col_4")
    rev = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"baseDimensionId": "Daily", "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert response.status_code == 200, response.text
    run = build_values(client)
    assert run["state"] == "succeeded", run
    for oid in ("Daily", "Weekly"):
        assert (
            client.get(BASE + "/dimensions/" + oid + "/table").json()["state"]
            == "ready"
        )
        sql_response = client.get(BASE + "/measures/execution/levels/" + oid + "/sql")
        assert sql_response.status_code == 200, sql_response.text
        assert "LEFT JOIN" in sql_response.json()["sql"]
        assert admin.command(
            "SELECT count() FROM (" + sql_response.json()["sql"] + ")"
        ) == (3 if oid == "Daily" else 2)

    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"dimensionId": "Daily", "columnId": "p"}],
            "columns": [],
            "measures": ["FQTY"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [["P1", "100.0"], ["P2", "60.0"]]
