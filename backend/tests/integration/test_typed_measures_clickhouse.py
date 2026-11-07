# ruff: noqa: F811
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import assert_post, build_values, transaction

pytestmark = pytest.mark.integration


def ref(client, oid, tx, native, column, non_time, time=None):
    return assert_post(
        client,
        "/measures",
        {
            "objectId": oid,
            "name": oid,
            "displayName": oid,
            "business": {},
            "definition": {
                "measureType": "referenced",
                "transactionId": tx,
                "nativeDimensionId": native,
                "valueColumnId": column,
                "aggregation": {"nonTime": non_time, "time": time or non_time},
            },
        },
    )


def calc(
    client, oid, native, expression, calculation="AggregateFirst", aggregation=None
):
    return assert_post(
        client,
        "/measures",
        {
            "objectId": oid,
            "name": oid,
            "displayName": oid,
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": native,
                "formula": {"displayText": oid, "expression": expression},
                "calculationType": calculation,
                **({"aggregation": aggregation} if aggregation else {}),
            },
        },
    )


def test_typed_native_rollups_formulas_attributes_and_mixed_states(clickhouse_app):
    client, _, _, _ = clickhouse_app
    sid = managed(
        client,
        "TypedFacts",
        b"product,location,status,dispatch,active,qty\nP1,L1,Healthy,2026-11-01,true,100\nP1,L2,At Risk,2026-11-03,false,50\nP2,L1,Healthy,2026-11-04,true,40\n",
    )
    created = client.post(
        BASE + "/transactions/from-grain",
        json={
            "objectId": "facts",
            "name": "Facts",
            "displayName": "Facts",
            "sourceId": sid,
            "grainColumnIds": ["col_1", "col_2"],
        },
    )
    assert created.status_code == 201, created.text
    level = created.json()["dimension"]
    lid = level["objectId"]
    product, location = level["definition"]["grain"]["keyIds"]
    dimsource = managed(
        client, "TypedProduct", b"product,brand\nP1,Brand A\nP2,Brand B\n"
    )
    assert_post(
        client,
        "/dimension-members",
        {
            "objectId": "Product",
            "name": "Product",
            "displayName": "Product",
            "isTimeDim": False,
            "definition": {
                "sourceId": dimsource,
                "columns": [
                    {
                        "columnId": "id",
                        "name": "product",
                        "displayName": "Product",
                        "sourceColumnId": "col_1",
                    },
                    {
                        "columnId": "brand",
                        "name": "brand",
                        "displayName": "Brand",
                        "sourceColumnId": "col_2",
                    },
                ],
                "keyColumnIds": ["id"],
                "attributeColumnIds": ["brand"],
                "labelColumnId": "id",
            },
        },
    )
    update = {
        key: value
        for key, value in level.items()
        if key not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    update["definition"]["memberBindings"] = [
        {
            "memberCatalogId": "Product",
            "keyMappings": [{"grainColumnId": product, "memberColumnId": "id"}],
            "selectedAttributeColumnIds": [],
        }
    ]
    response = client.put(
        BASE + "/dimensions/" + lid, json=update, headers={"If-Match": "1"}
    )
    assert response.status_code == 200, response.text
    create(
        client,
        {
            "objectId": "product_level",
            "name": "Product Level",
            "displayName": "Product Level",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourceDimensionId": lid,
                },
                "grain": {
                    "keyIds": [product],
                    "columns": [
                        {
                            "grainColumnId": product,
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": product},
                        }
                    ],
                },
            },
        },
    )
    assert build(client, lid, version=2)["state"] == "succeeded"
    assert build(client, "product_level")["state"] == "succeeded"
    assert (
        ref(client, "Status", "facts", lid, "col_3", "single_value")["definition"][
            "resultType"
        ]["type"]
        == "string"
    )
    assert (
        ref(client, "Dispatch", "facts", lid, "col_4", "max")["definition"][
            "resultType"
        ]["type"]
        == "date"
    )
    assert (
        ref(client, "Active", "facts", lid, "col_5", "any_true")["definition"][
            "resultType"
        ]["type"]
        == "boolean"
    )
    ref(client, "Qty", "facts", lid, "col_6", "sum")
    transaction(client, "coarse_facts", sid, "product_level", [(product, "col_1")])
    ref(
        client, "NativeStatus", "coarse_facts", "product_level", "col_3", "single_value"
    )
    shortage = {
        "type": "binary",
        "operator": "lt",
        "left": {"type": "measureRef", "measureId": "Qty"},
        "right": {"type": "literal", "value": 80},
    }
    assert (
        calc(client, "Shortage", lid, shortage)["definition"]["resultType"]["type"]
        == "boolean"
    )
    calc(
        client,
        "AnyShortage",
        lid,
        shortage,
        "CalculateFirst",
        {"nonTime": "any_true", "time": "any_true"},
    )
    calc(
        client,
        "Label",
        lid,
        {
            "type": "call",
            "function": "concat",
            "arguments": [
                {"type": "attributeRef", "memberCatalogId": "Product", "columnId": "brand"},
                {"type": "literal", "value": " / "},
                {"type": "attributeRef", "dimensionId": lid, "columnId": product},
            ],
        },
    )
    calc(
        client,
        "NextDispatch",
        lid,
        {
            "type": "call",
            "function": "add_days",
            "arguments": [
                {"type": "measureRef", "measureId": "Dispatch"},
                {"type": "literal", "value": 2},
            ],
        },
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    native = client.get(
        BASE + "/measures/execution/levels/" + lid + "?preview=true"
    ).json()
    assert all(
        column["role"] not in {"mixed", "coverage"} for column in native["columns"]
    )
    assert "NativeStatus" not in [
        column.get("measureId") for column in native["columns"]
    ]
    query = {
        "rows": [{"dimensionId": lid, "columnId": product}],
        "measures": [
            "Status",
            "Dispatch",
            "Active",
            "Shortage",
            "AnyShortage",
            "Label",
            "NextDispatch",
        ],
    }
    response = client.post(BASE + "/decision-view/query", json=query)
    assert response.status_code == 200, response.text
    result = response.json()
    p1 = next(
        (row, state)
        for row, state in zip(result["rows"], result["cellStates"])
        if row[0] == "P1"
    )
    assert p1[0][1] is None and p1[1][0] == "mixed"
    assert p1[0][2] == "2026-11-03"
    assert p1[0][3].lower() in {"1", "true"}
    assert p1[0][4].lower() in {"0", "false"}
    assert p1[0][5].lower() in {"1", "true"}
    assert p1[0][6] == "Brand A / P1"
    assert p1[0][7] == "2026-11-05"
    # A coarse referenced status remains native and rolls up; never copied to Base.
    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"dimensionId": "product_level", "columnId": product}],
            "measures": ["NativeStatus"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["cellStates"][0][0] == "mixed"

    # Reusing a coarser mixed representation must not lose its conflict marker.
    response = client.post(BASE + "/decision-view/query", json={"measures": ["Status"]})
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [[None]]
    assert response.json()["cellStates"] == [["mixed"]]
    capabilities = client.get(BASE + "/measures/NativeStatus/capabilities").json()
    assert not next(
        item for item in capabilities["levels"] if item["dimensionId"] == lid
    )["readable"]
    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"dimensionId": lid, "columnId": location}],
            "measures": ["NativeStatus"],
        },
    )
    assert response.status_code == 422

    # Numeric edits recompute typed dependents without dropping unrelated Mixed states.
    from .test_measures_clickhouse import update_measure
    from .test_managed_clickhouse import await_run
    from uuid import uuid4

    update_measure(
        client, "Qty", lambda definition: definition.update({"editable": True})
    )
    assert build_values(client)["state"] == "succeeded"
    view = {
        "rows": [{"dimensionId": lid, "columnId": product}],
        "measures": ["Qty"],
    }
    context = client.post(BASE + "/decision-view/query", json=view).json()[
        "editContext"
    ]
    run = await_run(
        client,
        BASE,
        client.post(
            BASE + "/decision-view/edits",
            json={
                "view": view,
                "measureId": "Qty",
                "memberValues": ["P1"],
                "value": 300,
                **context,
            },
            headers={"Idempotency-Key": "typed_" + uuid4().hex},
        ),
    )
    assert run["state"] == "succeeded", run
    response = client.post(BASE + "/decision-view/query", json=query)
    assert response.status_code == 200, response.text
    result = response.json()
    row_index = next(
        index for index, row in enumerate(result["rows"]) if row[0] == "P1"
    )
    assert result["cellStates"][row_index][0] == "mixed"
    assert result["rows"][row_index][5].lower() in {"0", "false"}


def test_typed_dates_survive_calendar_resolution(clickhouse_app):
    client, _, _, _ = clickhouse_app
    sid = managed(
        client,
        "TypedPromotions",
        b"product,start_date,end_date,status,qty\nP1,2026-01-01,2026-01-03,Active,90\n",
    )
    calendar = managed(
        client,
        "TypedCalendar",
        b"day,week\n2026-01-01,W1\n2026-01-02,W1\n2026-01-03,W2\n",
    )
    assert_post(
        client,
        "/dimension-members",
        {
            "objectId": "Time",
            "name": "Time",
            "displayName": "Time",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar,
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
    config = {
        "memberCatalogId": "Time",
        "startColumnId": "col_2",
        "endColumnId": "col_3",
        "selectedColumnIds": ["week"],
    }
    schema = client.post(
        BASE + "/transactions/calendar/schema",
        json={"transactionId": "Promo", "sourceId": sid, "calendarResolution": config},
    )
    assert schema.status_code == 200, schema.text
    week = next(
        column["columnId"]
        for column in schema.json()["columns"]
        if column["name"] == "week"
    )
    created = client.post(
        BASE + "/transactions/from-grain",
        json={
            "objectId": "Promo",
            "name": "Promo",
            "displayName": "Promo",
            "sourceId": sid,
            "grainColumnIds": ["col_1", week],
            "calendarResolution": config,
        },
    )
    assert created.status_code == 201, created.text
    level = created.json()["dimension"]["objectId"]
    assert build(client, level)["state"] == "succeeded"
    ref(client, "Start", "Promo", level, "col_2", "min")
    ref(client, "Status", "Promo", level, "col_4", "single_value")
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data = client.get(
        BASE + "/measures/execution/levels/" + level + "?preview=true"
    ).json()
    names = [
        column["measureId"] for column in data["columns"] if column.get("measureId")
    ]
    for row in data["rows"]:
        output = dict(zip(names, row[-len(names) :]))
        assert output == {"Start": "2026-01-01", "Status": "Active"}


@pytest.mark.parametrize(
    "method,mixed_day,expected",
    [
        ("last", "2026-01-01", "C"),
        ("last", "2026-01-02", None),
        ("first", "2026-01-01", None),
        ("first", "2026-01-02", "C"),
    ],
)
def test_ordered_typed_aggregation_carries_the_selected_cells_state(
    clickhouse_app, method, mixed_day, expected
):
    client, _, _, _ = clickhouse_app
    other = "2026-01-02" if mixed_day == "2026-01-01" else "2026-01-01"
    sid = managed(
        client,
        "OrderedTyped",
        (
            "product,day,status\nP1,"
            + mixed_day
            + ",A\nP1,"
            + mixed_day
            + ",B\nP1,"
            + other
            + ",C\n"
        ).encode(),
    )
    calendar = managed(client, "OrderedCalendar", b"day\n2026-01-01\n2026-01-02\n")
    assert_post(
        client,
        "/dimension-members",
        {
            "objectId": "Time",
            "name": "Time",
            "displayName": "Time",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar,
                "columns": [
                    {
                        "columnId": "day",
                        "name": "day",
                        "displayName": "Day",
                        "sourceColumnId": "col_1",
                    }
                ],
                "keyColumnIds": ["day"],
                "labelColumnId": "day",
                "attributeColumnIds": [],
                "time": {
                    "baseGrain": "day",
                    "dateColumnId": "day",
                    "orderingColumnId": "day",
                },
            },
        },
    )
    created = client.post(
        BASE + "/transactions/from-grain",
        json={
            "objectId": "events",
            "name": "Events",
            "displayName": "Events",
            "sourceId": sid,
            "grainColumnIds": ["col_1", "col_2"],
        },
    )
    assert created.status_code == 201, created.text
    level = created.json()["dimension"]
    lid = level["objectId"]
    product, day = level["definition"]["grain"]["keyIds"]
    payload = {
        key: value
        for key, value in level.items()
        if key not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    payload["definition"]["memberBindings"] = [
        {
            "memberCatalogId": "Time",
            "keyMappings": [{"grainColumnId": day, "memberColumnId": "day"}],
            "selectedAttributeColumnIds": [],
        }
    ]
    response = client.put(
        BASE + "/dimensions/" + lid, json=payload, headers={"If-Match": "1"}
    )
    assert response.status_code == 200, response.text
    create(
        client,
        {
            "objectId": "product",
            "name": "Product",
            "displayName": "Product",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourceDimensionId": lid,
                },
                "grain": {
                    "keyIds": [product],
                    "columns": [
                        {
                            "grainColumnId": product,
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": product},
                        }
                    ],
                },
            },
        },
    )
    assert build(client, lid, version=2)["state"] == "succeeded"
    assert build(client, "product")["state"] == "succeeded"
    ref(client, "Status", "events", lid, "col_3", "single_value", method)
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"dimensionId": "product", "columnId": product}],
            "measures": ["Status"],
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == [["P1", expected]]
    assert result.json()["cellStates"] == [["mixed" if expected is None else None]]


def test_adding_typed_formula_preserves_legacy_numeric_edit(
    clickhouse_app, monkeypatch
):
    import backend.app.engine.measure_compiler as compiler
    from .test_measures_clickhouse import update_measure
    from .test_managed_clickhouse import await_run
    from uuid import uuid4

    client, _, _, _ = clickhouse_app
    sid = managed(client, "LegacyTypedUpgrade", b"product,qty\nP1,10\n")
    response = client.post(
        BASE + "/transactions/from-grain",
        json={
            "objectId": "Legacy",
            "name": "Legacy",
            "displayName": "Legacy",
            "sourceId": sid,
            "grainColumnIds": ["col_1"],
        },
    )
    assert response.status_code == 201, response.text
    level = response.json()["dimension"]
    lid = level["objectId"]
    product = level["definition"]["grain"]["keyIds"][0]
    assert build(client, lid)["state"] == "succeeded"
    ref(client, "Qty", "Legacy", lid, "col_2", "sum")
    update_measure(
        client, "Qty", lambda definition: definition.update({"editable": True})
    )
    current_version = compiler.MEASURE_COMPILER_VERSION
    monkeypatch.setattr(
        compiler, "MEASURE_COMPILER_VERSION", compiler.LEGACY_MEASURE_COMPILER_VERSION
    )
    assert build_values(client)["state"] == "succeeded"
    view = {
        "rows": [{"dimensionId": lid, "columnId": product}],
        "measures": ["Qty"],
    }
    context = client.post(BASE + "/decision-view/query", json=view).json()[
        "editContext"
    ]
    edit = await_run(
        client,
        BASE,
        client.post(
            BASE + "/decision-view/edits",
            json={
                "view": view,
                "measureId": "Qty",
                "memberValues": ["P1"],
                "value": 99,
                **context,
            },
            headers={"Idempotency-Key": "typed_" + uuid4().hex},
        ),
    )
    assert edit["state"] == "succeeded", edit
    monkeypatch.setattr(compiler, "MEASURE_COMPILER_VERSION", current_version)
    calc(
        client,
        "Status",
        lid,
        {
            "type": "call",
            "function": "if",
            "arguments": [
                {
                    "type": "binary",
                    "operator": "gt",
                    "left": {"type": "measureRef", "measureId": "Qty"},
                    "right": {"type": "literal", "value": 50},
                },
                {"type": "literal", "value": "High"},
                {"type": "literal", "value": "Low"},
            ],
        },
    )
    revision = client.get(BASE).json()
    run = await_run(
        client,
        BASE,
        client.post(
            BASE + "/measures/execution/build",
            json={"mode": "incremental"},
            headers={
                "If-Match": str(revision["version"]),
                "Idempotency-Key": "typed_" + uuid4().hex,
            },
        ),
    )
    assert run["state"] == "succeeded", run
    response = client.post(
        BASE + "/decision-view/query", json={**view, "measures": ["Qty", "Status"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [["P1", "99.0", "High"]]
