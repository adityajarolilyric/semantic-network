# ruff: noqa: F811
from copy import deepcopy
import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, build, table
from .test_measures_clickhouse import measure, build_values, transaction
from .test_managed_clickhouse import await_run
from backend.app.schemas.decision_view import DecisionViewField

pytestmark = pytest.mark.integration


def onboarding(source, oid="Forecast"):
    return {
        "objectId": oid,
        "name": oid,
        "displayName": oid,
        "sourceId": source,
        "grainColumnIds": ["col_1", "col_2", "col_3"],
        "dimensionName": oid + " grain",
    }


def post(client, payload):
    response = client.post(BASE + "/transactions/from-grain", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_one_table_no_dimensions_pivot_edit_and_sales_reuse(clickhouse_app):
    client, _, _, _ = clickhouse_app
    sid = managed(
        client,
        "ForecastOnly",
        b"product_id,location_id,time_id,forecast_qty\nP1,L1,W1,100\nP1,L1,W1,50\nP2,L1,W1,200\n",
    )
    payload = onboarding(sid)
    before = client.get(BASE).json()
    response = client.post(BASE + "/transactions/from-grain/preview", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["rowCount"] == 2
    assert client.get(BASE).json()["version"] == before["version"]
    assert client.get(BASE + "/dimensions").json()["items"] == []
    created = post(client, payload)
    level = created["dimension"]
    lid = level["objectId"]
    mappings = created["transaction"]["definition"]["keyMappings"]
    assert created["transaction"]["definition"]["mappingMode"] == "direct"
    assert level["definition"]["memberBindings"] == []
    assert client.get(BASE).json()["baseDimensionId"] == lid
    version = client.get(BASE).json()["version"]
    assert post(client, payload) == created
    assert client.get(BASE).json()["version"] == version
    assert client.get(BASE + "/dimension-members").json()["items"] == []
    assert build(client, lid)["state"] == "succeeded"
    assert table(client, lid)["rows"] == [["P1", "L1", "W1"], ["P2", "L1", "W1"]]
    measure(client, "FQTY", "Forecast", lid, "col_4", mode="weighted_by_self")
    # Enable editing using the normal measure update contract.
    m = client.get(BASE + "/measures/FQTY").json()
    editable = {
        k: v
        for k, v in m.items()
        if k not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    editable["definition"]["editable"] = True
    response = client.put(
        BASE + "/measures/FQTY", json=editable, headers={"If-Match": str(m["version"])}
    )
    assert response.status_code == 200, response.text
    assert build_values(client)["state"] == "succeeded"
    options = client.get(BASE + "/decision-view/options").json()
    assert (
        options["dimensionMembers"] == []
        and options["dimensions"][0]["dimensionId"] == lid
    )
    product = next(
        m["grainColumnId"] for m in mappings if m["sourceColumnId"] == "col_1"
    )
    view = {
        "rows": [{"dimensionId": lid, "columnId": product}],
        "columns": [],
        "measures": ["FQTY"],
    }
    response = client.post(BASE + "/decision-view/query", json=view)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["rows"] == [["P1", "150.0"], ["P2", "200.0"]]
    assert result["measures"][0]["editable"] is True
    edit = client.post(
        BASE + "/decision-view/edits",
        json={
            "view": view,
            "measureId": "FQTY",
            "memberValues": ["P1"],
            "value": 180,
            "generationId": result["editContext"]["generationId"],
            "revisionVersion": result["editContext"]["revisionVersion"],
        },
        headers={"Idempotency-Key": "edit_unbound"},
    )
    run = await_run(client, BASE, edit)
    assert run["state"] == "succeeded", run
    assert client.post(BASE + "/decision-view/query", json=view).json()["rows"][0] == [
        "P1",
        "180.0",
    ]
    sales = managed(
        client,
        "SalesOnly",
        b"product_id,location_id,time_id,sales_qty\nP1,L1,W1,40\nP1,L1,W1,20\nP2,L1,W1,70\n",
    )
    transaction(
        client,
        "Sales",
        sales,
        lid,
        [(m["grainColumnId"], m["sourceColumnId"]) for m in mappings],
    )
    measure(client, "SQTY", "Sales", lid, "col_4")
    assert build_values(client)["state"] == "succeeded"
    view["measures"].append("SQTY")
    response = client.post(BASE + "/decision-view/query", json=view)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [["P1", "150.0", "60.0"], ["P2", "200.0", "70.0"]]
    assert client.get(BASE + "/dimensions").json()["items"][0]["objectId"] == lid
    assert client.get(BASE + "/dimension-members").json()["items"] == []


def test_replay_conflict_validation_and_missing_sales_intersections(clickhouse_app):
    client, _, _, _ = clickhouse_app
    sid = managed(
        client, "InitialForecast", b"product,location,time,qty\nP1,L1,W1,10\n"
    )
    payload = onboarding(sid)
    invalid = deepcopy(payload)
    invalid["grainColumnIds"] = ["missing"]
    assert (
        client.post(BASE + "/transactions/from-grain", json=invalid).status_code == 422
    )
    assert client.get(BASE + "/dimensions").json()["items"] == []
    created = post(client, payload)
    invalid = deepcopy(payload)
    invalid["grainColumnIds"] = ["col_1"]
    assert (
        client.post(BASE + "/transactions/from-grain", json=invalid).status_code == 409
    )
    lid = created["dimension"]["objectId"]
    assert build(client, lid)["state"] == "succeeded"
    other = post(client, onboarding(sid, "Other"))
    assert client.get(BASE).json()["baseDimensionId"] == lid
    assert build(client, other["dimension"]["objectId"])["state"] == "succeeded"
    sale = managed(
        client, "OutOfPopulationSales", b"product,location,time,qty\nP2,L1,W1,5\n"
    )
    transaction(
        client,
        "Sales",
        sale,
        lid,
        [
            (m["grainColumnId"], m["sourceColumnId"])
            for m in created["transaction"]["definition"]["keyMappings"]
        ],
    )
    measure(client, "SQTY", "Sales", lid, "col_4")
    result = build_values(client)
    assert result["state"] == "failed", result
    assert result["error"]["code"] == "unmatched_transaction_grain"
    assert table(client, lid)["rows"] == [["P1", "L1", "W1"]]
    # The user replaces the population explicitly; existing grain/mapping IDs survive.
    replacement = managed(
        client, "ValidIntersections", b"time,product,location\nW1,P1,L1\nW1,P2,L1\n"
    )
    level_doc = client.get(BASE + "/dimensions/" + lid).json()
    definition = {
        k: v
        for k, v in level_doc.items()
        if k not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    definition["definition"]["population"] = {
        "operation": "source",
        "sourceId": replacement,
    }
    for column, cid in zip(
        definition["definition"]["grain"]["columns"], ["col_2", "col_3", "col_1"]
    ):
        column["populationRef"] = {"columnId": cid}
    updated = client.put(
        BASE + "/dimensions/" + lid,
        json=definition,
        headers={"If-Match": str(level_doc["version"])},
    )
    assert updated.status_code == 200, updated.text
    assert build(client, lid, version=updated.json()["version"])["state"] == "succeeded"
    assert build_values(client)["state"] == "succeeded"
    assert (
        client.get(BASE + "/transactions/Forecast").json()["definition"]["keyMappings"]
        == created["transaction"]["definition"]["keyMappings"]
    )


def test_field_owner_is_exclusive():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DecisionViewField(columnId="key")
    with pytest.raises(ValidationError):
        DecisionViewField(columnId="key", memberCatalogId="d", dimensionId="p")
    assert DecisionViewField(columnId="key", memberCatalogId="d").model_dump() == {
        "columnId": "key",
        "memberCatalogId": "d",
    }


@pytest.mark.parametrize(
    "with_pivot,with_calendar", [(True, False), (False, True), (True, True)]
)
def test_onboarding_uses_transformed_population_and_preserves_input(
    clickhouse_app, with_pivot, with_calendar
):
    client, _, _, _ = clickhouse_app
    sid = managed(
        client,
        "PromotionsFirst",
        b"attribute,member,start_date,end_date,qty\nproduct_id,P1,2026-01-01,2026-01-03,90\nproduct_id,P2,2026-01-02,2026-01-03,60\n",
    )
    calendar_source = managed(
        client,
        "CalendarFirst",
        b"day,week\n2026-01-01,WA\n2026-01-02,WA\n2026-01-03,WB\n",
    )
    response = client.post(
        BASE + "/dimension-members",
        json={
            "objectId": "Time",
            "kind": "dimensionMembers",
            "name": "Time",
            "displayName": "Time",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar_source,
                "columns": [
                    {
                        "columnId": "day",
                        "name": "day",
                        "displayName": "Day",
                        "sourceColumnId": "col_1",
                    },
                    {
                        "columnId": "week",
                        "name": "week",
                        "displayName": "Week",
                        "sourceColumnId": "col_2",
                    },
                ],
                "keyColumnIds": ["day"],
                "labelColumnId": "day",
                "attributeColumnIds": ["week"],
                "time": {
                    "baseGrain": "day",
                    "dateColumnId": "day",
                    "orderingColumnId": "day",
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    # A calendar must be discoverable before a native dimension exists.
    options = client.get(BASE + "/transactions/resolution/options")
    assert options.status_code == 200, options.text
    assert options.json()["calendars"][0]["memberCatalogId"] == "Time"
    assert options.json()["targets"] == []
    pivot = (
        {
            "stageId": "pivot_first",
            "type": "pivot",
            "enabled": True,
            "recordKeyColumnIds": [],
            "groupByColumnIds": ["col_3", "col_4", "col_5"],
            "pairs": [
                {
                    "pairId": "product_pair",
                    "attributeColumnId": "col_1",
                    "valueColumnId": "col_2",
                    "outputs": [{"attributeValue": "product_id", "name": "product_id"}],
                }
            ],
        }
        if with_pivot
        else None
    )
    calendar = (
        {
            "memberCatalogId": "Time",
            "startColumnId": "col_3",
            "endColumnId": "col_4",
            "selectedColumnIds": ["week"],
        }
        if with_calendar
        else None
    )
    owner = {"objectId": "Promotion", "sourceId": sid, "pivot": pivot}
    schema = client.post(BASE + "/transactions/input/schema", json=owner)
    assert schema.status_code == 200, schema.text
    columns = schema.json()["columns"]
    if calendar:
        schema = client.post(
            BASE + "/transactions/calendar/schema",
            json={
                "transactionId": "Promotion",
                "sourceId": sid,
                "pivot": pivot,
                "calendarResolution": calendar,
            },
        )
        assert schema.status_code == 200, schema.text
        columns = schema.json()["columns"]
    product = next(
        c["columnId"]
        for c in columns
        if c["name"] == ("product_id" if with_pivot else "member")
    )
    grain = [product]
    if calendar:
        grain.append(next(c["columnId"] for c in columns if c["name"] == "week"))
    payload = {
        **owner,
        "name": "Promotion",
        "displayName": "Promotion",
        "grainColumnIds": grain,
        "calendarResolution": calendar,
    }
    before = client.get(BASE).json()["version"]
    preview = client.post(BASE + "/transactions/from-grain/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["rowCount"] == (4 if calendar else 2)
    filtered_preview = client.post(
        BASE + "/transactions/from-grain/preview",
        json={
            **payload,
            "filters": {
                "root": {
                    "type": "group",
                    "combinator": "and",
                    "children": [
                        {
                            "type": "condition",
                            "columnId": "col_5",
                            "operator": "gt",
                            "values": [70],
                        }
                    ],
                }
            },
        },
    )
    assert filtered_preview.status_code == 200, filtered_preview.text
    assert filtered_preview.json()["rowCount"] == (3 if calendar else 1)
    assert client.get(BASE).json()["version"] == before
    assert [d["name"] for d in client.get(BASE + "/dimensions").json()["items"]] == [
        "Time"
    ]
    created = post(client, payload)
    level = created["dimension"]
    lid = level["objectId"]
    config = level["definition"]["population"]["inputTransform"]
    assert config["objectId"] == "Promotion"
    assert config["pivot"] == created["transaction"]["definition"].get("pivot")
    assert config["calendarResolution"] == created["transaction"]["definition"].get(
        "calendarResolution"
    )
    assert post(client, payload) == created
    run = build(client, lid)
    assert run["state"] == "succeeded", run
    assert table(client, lid)["rowCount"] == (4 if calendar else 2)
    # The ordinary Dimension APIs can infer transformed key types and rebuild it.
    response = client.post(
        BASE + "/dimensions/schema",
        json={
            k: v
            for k, v in level.items()
            if k not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
        },
    )
    assert response.status_code == 200, response.text
    measure(client, "PromoQty", "Promotion", lid, "col_5")
    run = build_values(client)
    assert run["state"] == "succeeded", run
    assert len(
        client.get(BASE + "/decision-view/options").json()["dimensions"][0]["columns"]
    ) >= len(grain)
    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [
                {"dimensionId": lid, "columnId": key}
                for key in level["definition"]["grain"]["keyIds"]
            ],
            "columns": [],
            "measures": ["PromoQty"],
        },
    )
    assert response.status_code == 200, response.text
    expected = (
        [
            ["P1", "WA", "180.0"],
            ["P1", "WB", "90.0"],
            ["P2", "WA", "60.0"],
            ["P2", "WB", "60.0"],
        ]
        if calendar
        else [["P1", "90.0"], ["P2", "60.0"]]
    )
    assert response.json()["rows"] == expected
    # Invalid ranges execute the same checks and never replace the saved definition.
    if calendar:
        invalid = deepcopy(payload)
        invalid["calendarResolution"]["startColumnId"] = "col_4"
        invalid["calendarResolution"]["endColumnId"] = "col_3"
        response = client.post(BASE + "/transactions/from-grain/preview", json=invalid)
        assert response.status_code == 422, response.text
        assert "invalid_resolution_range" in response.text
        unchanged = client.get(BASE + "/dimensions/" + lid).json()
        assert unchanged["definition"] == level["definition"]
        assert unchanged["version"] == level["version"]


def test_onboarding_filters_preview_and_materialise_without_changing_population(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    source = managed(
        client,
        "FilteredForecast",
        b"product_id,location_id,time_id,qty\nP1,L1,W1,100\nP1,L1,W1,-10\nP2,L1,W1,-20\n",
    )
    payload = onboarding(source, "FilteredTx")
    payload["filters"] = {
        "root": {
            "type": "group",
            "combinator": "and",
            "children": [
                {
                    "type": "condition",
                    "columnId": "col_4",
                    "operator": "gt",
                    "values": [0],
                }
            ],
        }
    }
    before = client.get(BASE).json()["version"]
    response = client.post(BASE + "/transactions/from-grain/preview", json=payload)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["rowCount"] == 1
    assert preview["rows"] == [["P1", "L1", "W1", "100"]]
    assert preview["diagnostics"]["filteredOutRows"] == 2
    assert client.get(BASE).json()["version"] == before
    assert client.get(BASE + "/transactions").json()["items"] == []
    created = post(client, payload)
    assert created["transaction"]["definition"]["filters"] == payload["filters"]
    level = created["dimension"]["objectId"]
    assert build(client, level)["state"] == "succeeded"
    assert table(client, level)["rowCount"] == 2
    # The resource envelope is not part of a typed preview input.
    transaction_input = {
        key: value
        for key, value in created["transaction"].items()
        if key not in ["modelId", "revisionId", "version", "createdAt", "updatedAt"]
    }
    normal = client.post(
        BASE + "/transactions/resolution/preview", json=transaction_input
    )
    assert normal.status_code == 200, normal.text
    assert normal.json()["rows"] == preview["rows"]
    measure(client, "FilteredQty", "FilteredTx", level, "col_4")
    assert build_values(client)["state"] == "succeeded"
    values = client.get(
        BASE + "/measures/execution/levels/" + level + "?preview=true"
    ).json()
    assert sorted(row[-1] for row in values["rows"] if row[-1] is not None) == ["100.0"]


@pytest.mark.parametrize("mapped", [False, True])
def test_create_multiple_measures_and_materialise_configured_policies(
    clickhouse_app, mapped
):
    client, _, _, _ = clickhouse_app
    source = managed(
        client,
        "MultiMeasure",
        b"product,location,week,qty,price\nP1,L1,W1,100,10\nP1,L1,W1,50,20\nP2,L1,W1,200,40\n",
    )
    selections = [
        {
            "valueColumnId": "col_4",
            "editable": True,
            "aggregation": {"nonTime": "sum", "time": "sum"},
        },
        {
            "valueColumnId": "col_5",
            "aggregation": {"nonTime": "avg", "time": "max"},
            "allocation": {"mode": "none"},
        },
    ]
    payload = onboarding(source, "Multi")
    if mapped:
        existing = post(client, onboarding(source, "Initial"))
        level = existing["dimension"]["objectId"]
        payload = {
            k: v
            for k, v in existing["transaction"].items()
            if k not in ["modelId", "revisionId", "createdAt", "updatedAt", "version"]
        }
        payload["objectId"] = "Multi"
        payload["measures"] = selections
        response = client.post(BASE + "/transactions", json=payload)
        assert response.status_code == 201, response.text
    else:
        payload["measures"] = selections
        level = post(client, payload)["dimension"]["objectId"]
    assert build(client, level)["state"] == "succeeded"
    measures = client.get(BASE + "/measures").json()["items"]
    assert sorted(m["name"] for m in measures) == ["price", "qty"]
    assert build_values(client)["state"] == "succeeded"
    result = client.get(
        BASE + "/measures/execution/levels/" + level + "?preview=true"
    ).json()
    assert result["rowCount"] == 2
    # Distinct configured aggregations must be honoured during native source grouping.
    assert sorted(
        sorted(float(value) for value in row[-2:]) for row in result["rows"]
    ) == [[15.0, 150.0], [40.0, 200.0]]
