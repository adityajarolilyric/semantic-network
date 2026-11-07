# ruff: noqa: F811
import pytest
from .test_csv_clickhouse import authored_clickhouse_app, clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import assert_post, measure, build_values, values
from .test_decision_view_clickhouse import dimension

pytestmark = pytest.mark.integration


def setup_resolution(
    client,
    rows=b"brand,A,zone,N,100,10\nid,P1,id,L1,30,20\n",
    mode="split",
    *,
    build_native=True,
    grain_names=("product", "location"),
):
    pop = managed(client, "NativeRows", b"product,location\nP1,L1\nP2,L1\nP3,L2\n")
    products = managed(client, "Products", b"product,brand\nP1,A\nP2,A\nP3,B\n")
    locations = managed(client, "Locations", b"location,zone\nL1,N\nL2,S\n")
    dimension(client, "Product", products, ["product", "brand"])
    dimension(client, "Location", locations, ["location", "zone"])
    create(
        client,
        {
            "objectId": "native",
            "kind": "dimension",
            "name": "PL",
            "displayName": "PL",
            "definition": {
                "population": {"operation": "source", "sourceId": pop},
                "grain": {
                    "keyIds": ["product", "location"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": grain_names[0],
                            "populationRef": {"columnId": "col_1"},
                        },
                        {
                            "grainColumnId": "location",
                            "name": grain_names[1],
                            "populationRef": {"columnId": "col_2"},
                        },
                    ],
                },
                "memberBindings": [
                    {
                        "memberCatalogId": "Product",
                        "keyMappings": [
                            {"grainColumnId": "product", "memberColumnId": "product"}
                        ],
                        "selectedAttributeColumnIds": [],
                    },
                    {
                        "memberCatalogId": "Location",
                        "keyMappings": [
                            {
                                "grainColumnId": "location",
                                "memberColumnId": "location",
                            }
                        ],
                        "selectedAttributeColumnIds": [],
                    },
                ],
            },
        },
    )
    rev = client.get(BASE).json()
    assert (
        client.put(
            BASE,
            json={"baseDimensionId": "native", "changeReason": ""},
            headers={"If-Match": str(rev["version"])},
        ).status_code
        == 200
    )
    if build_native:
        assert build(client, "native")["state"] == "succeeded"
    source = managed(
        client,
        "MixedAssignments",
        b"p_level,p_value,l_level,l_value,quantity,sequence\n" + rows,
    )

    def rule(rule_id, selector, value, dim, columns):
        return {
            "ruleId": rule_id,
            "kind": "level_value",
            "selectorColumnId": selector,
            "valueColumnId": value,
            "selectors": [
                {"value": literal, "target": {"memberCatalogId": dim, "columnId": col}}
                for literal, col in columns
            ],
        }

    return {
        "objectId": "mixed",
        "kind": "transaction",
        "name": "Mixed assignments",
        "displayName": "Mixed assignments",
        "definition": {
            "sourceId": source,
            "dimensionId": "native",
            "mappingMode": "resolve_multi_grain",
            "keyMappings": [],
            "resolution": {
                "axes": [
                    {
                        "grainColumnId": "product",
                        "rules": [
                            rule(
                                "r_product",
                                "col_1",
                                "col_2",
                                "Product",
                                [("id", "product"), ("brand", "brand")],
                            )
                        ],
                    },
                    {
                        "grainColumnId": "location",
                        "rules": [
                            rule(
                                "r_location",
                                "col_3",
                                "col_4",
                                "Location",
                                [("id", "location"), ("zone", "zone")],
                            )
                        ],
                    },
                ],
                "values": [{"sourceColumnId": "col_5", "mode": mode}],
                "orderColumnIds": ["col_6"],
            },
        },
    }


def preview(client, tx):
    return client.post(BASE + "/transactions/resolution/preview", json=tx)


def by_cell(result):
    indices = {c["columnId"]: i for i, c in enumerate(result["columns"])}
    keys = [i for i, c in enumerate(result["columns"]) if c.get("grainColumnId")]
    return {
        tuple(row[i] for i in keys): row[indices["col_5"]] for row in result["rows"]
    }


@pytest.mark.parametrize("mode,expected", [("split", "50.0"), ("copy", "100.0")])
def test_resolution_split_copy_and_measure_loading(
    authored_clickhouse_app, mode, expected
):
    client, admin, database, _ = authored_clickhouse_app
    tx = setup_resolution(client, mode=mode)
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "30.0", ("P2", "L1"): expected}
    assert result.json()["diagnostics"]["overwrittenMatches"] == 1
    assert client.get(BASE + "/transactions/mixed").status_code == 404

    # Seed a pre-upgrade definition: new CRUD forbids creating Level/Value rules.
    async def seed_legacy(repo):
        from backend.app.schemas.transactions import TransactionInput
        from backend.app.services.base import now, scope

        query = scope("default_model", "revision_1")
        service = client.app.state.container.transactions
        await service.touch_revision(repo, "default_model", "revision_1")
        await repo.insert(
            "semantic_objects",
            {
                **TransactionInput.model_validate(tx).model_dump(),
                **query,
                "version": 1,
                "createdAt": now(),
                "updatedAt": now(),
            },
        )

    client.portal.call(client.app.state.container.store.transaction, seed_legacy)

    schema = client.get(BASE + "/transactions/mixed/schema")
    assert schema.status_code == 200, schema.text
    measure(client, "QTY", "mixed", "native", "col_5")
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "native")
    assert [r[cols["QTY"]] for r in data["rows"]] == ["30.0", expected, None]
    count = int(
        admin.command(
            "SELECT count() FROM system.tables WHERE database={db:String} AND engine='MergeTree' AND startsWith(comment,'semanticTable:')",
            parameters={"db": database},
        )
    )
    bindings = client.app.state.container.store.documents["engine_artifact_bindings"]
    populations = [
        binding for binding in bindings if binding.get("resourceKind") == "dimension"
    ]
    value_bindings = [
        binding
        for binding in bindings
        if binding.get("resourceKind") == "measureValues"
    ]
    # Lookup-only Dimensions have populations, not measure representations.
    assert {binding["resourceId"] for binding in value_bindings} == {"native"}
    assert count == len(populations) + len(value_bindings)


def test_resolution_later_coarse_wins_regardless_of_input_storage_order(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(client, b"brand,A,zone,N,100,20\nid,P1,id,L1,30,10\n")
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "50.0", ("P2", "L1"): "50.0"}


@pytest.mark.parametrize(
    "rows,code",
    [
        (b"brand,A,zone,N,100,10\nid,P1,id,L1,30,10\n", "ambiguous_resolution_order"),
        (b"unknown,A,zone,N,100,10\n", "invalid_resolution_selector"),
        (b"brand,MISSING,zone,N,100,10\n", "unmatched_resolution_row"),
        (b",,zone,N,100,10\n", "missing_resolution_constraint"),
    ],
)
def test_resolution_rejects_ambiguous_or_unmatched_assignments(
    authored_clickhouse_app, rows, code
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(client, rows)
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == code


def test_resolution_reduces_same_constraints_without_changing_split_denominator(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(
        client, b"brand,A,zone,N,100,10\nbrand,A,zone,N,200,20\nid,P1,id,L1,30,30\n"
    )
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "30.0", ("P2", "L1"): "100.0"}
    assert result.json()["diagnostics"]["shadowedAssignments"] == 1


def test_resolution_null_winner_is_not_filled_from_earlier_row(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(client, b"brand,A,zone,N,100,10\nid,P1,id,L1,,20\n")
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): None, ("P2", "L1"): "50.0"}


def test_resolution_expansion_limit_is_not_silent_truncation(authored_clickhouse_app):
    client, _, _, settings = authored_clickhouse_app
    tx = setup_resolution(client)
    settings.resolution_max_rows = 1
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == "resolution_expansion_limit"


def test_resolution_fixed_columns_and_explicit_all_members(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(client, b"id,P1,id,L1,60,10\n")
    axes = tx["definition"]["resolution"]["axes"]
    axes[0]["rules"] = [
        {
            "ruleId": "fixed",
            "kind": "column",
            "sourceColumnId": "col_2",
            "target": {"memberCatalogId": "Product", "columnId": "product"},
        }
    ]
    axes[1]["rules"] = []
    axes[1]["allowAll"] = True
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "60.0"}


def test_resolution_string_tie_breaker_and_independent_partitions(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(client, b"brand,A,zone,N,100,10\nid,P1,id,L1,30,10\n")
    config = tx["definition"]["resolution"]
    config["orderColumnIds"].append("col_1")
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "30.0", ("P2", "L1"): "50.0"}
    config["orderColumnIds"] = ["col_6"]
    config["partitionColumnIds"] = ["col_1"]
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert result.json()["diagnostics"]["resolvedRows"] == 3
    assert result.json()["diagnostics"]["overwrittenMatches"] == 0


def setup_date_range(
    client,
    rows=b"140,2026-01-01,2026-01-07,10\n",
    *,
    day=False,
    population=None,
    calendar_rows=None,
    month=False,
):
    calendar = managed(
        client,
        "DailyCalendar",
        calendar_rows
        or b"day,week\n2026-01-01,FISCAL_A\n2026-01-02,FISCAL_A\n2026-01-03,FISCAL_A\n2026-01-04,FISCAL_B\n2026-01-05,FISCAL_B\n2026-01-06,FISCAL_B\n2026-01-07,FISCAL_B\n",
    )
    assert_post(
        client,
        "/dimension-members",
        {
            "objectId": "Time",
            "kind": "dimensionMembers",
            "name": "Calendar",
            "displayName": "Calendar",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar,
                "columns": [
                    {
                        "columnId": "day",
                        "name": "day",
                        "displayName": "Date",
                        "sourceColumnId": "col_1",
                    },
                    {
                        "columnId": "week",
                        "name": "week",
                        "displayName": "Fiscal week",
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
    if month:
        current = client.get(BASE + "/dimension-members/Time").json()
        current["definition"]["columns"].append(
            {
                "columnId": "month",
                "name": "month",
                "displayName": "Month",
                "sourceColumnId": "col_3",
            }
        )
        current["definition"]["attributeColumnIds"].append("month")
        payload = {
            k: v
            for k, v in current.items()
            if k not in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
        }
        response = client.put(
            BASE + "/dimension-members/Time",
            json=payload,
            headers={"If-Match": str(current["version"])},
        )
        assert response.status_code == 200, response.text
    if population is None:
        periods = (
            [f"2026-01-0{i}" for i in range(1, 8)] if day else ["FISCAL_A", "FISCAL_B"]
        )
        population = (
            b"product,period\n"
            + "".join(f"{p},{t}\n" for p in ("P1", "P2") for t in periods).encode()
        )
    pop = managed(client, "PeriodPopulation", population)
    create(
        client,
        {
            "objectId": "period_native",
            "kind": "dimension",
            "name": "Product Period",
            "displayName": "Product Period",
            "definition": {
                "population": {"operation": "source", "sourceId": pop},
                "grain": {
                    "keyIds": ["product", "period"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "Product",
                            "populationRef": {"columnId": "col_1"},
                        },
                        {
                            "grainColumnId": "period",
                            "name": "Period",
                            "populationRef": {"columnId": "col_2"},
                        },
                    ],
                },
                "memberBindings": [
                    {
                        "memberCatalogId": "Time",
                        "keyMappings": [
                            {
                                "grainColumnId": "period",
                                "memberColumnId": "day" if day else "week",
                            }
                        ],
                        "selectedAttributeColumnIds": [],
                    }
                ],
            },
        },
    )
    if month:
        current = client.get(BASE + "/dimensions/period_native").json()
        current["definition"]["grain"]["keyIds"].append("month")
        current["definition"]["grain"]["columns"].append(
            {
                "grainColumnId": "month",
                "name": "Month",
                "populationRef": {"columnId": "col_3"},
            }
        )
        current["definition"]["memberBindings"][0]["keyMappings"].append(
            {"grainColumnId": "month", "memberColumnId": "month"}
        )
        payload = {
            k: v
            for k, v in current.items()
            if k not in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
        }
        response = client.put(
            BASE + "/dimensions/period_native",
            json=payload,
            headers={"If-Match": str(current["version"])},
        )
        assert response.status_code == 200, response.text
    rev = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"baseDimensionId": "period_native", "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert response.status_code == 200, response.text
    assert (
        build(client, "period_native", version=2 if month else 1)["state"]
        == "succeeded"
    )
    source = managed(
        client, "DateAssignments", b"quantity,start_date,end_date,sequence\n" + rows
    )
    return {
        "objectId": "date_tx",
        "kind": "transaction",
        "name": "Date assignments",
        "displayName": "Date assignments",
        "definition": {
            "sourceId": source,
            "dimensionId": "period_native",
            "mappingMode": "resolve_multi_grain",
            "keyMappings": [],
            "resolution": {
                "axes": [
                    {"grainColumnId": "product", "allowAll": True, "rules": []},
                    {
                        "grainColumnId": "period",
                        "dateRange": {
                            "memberCatalogId": "Time",
                            "calendarKeyColumnId": "day" if day else "week",
                            "startColumnId": "col_2",
                            "endColumnId": "col_3",
                        },
                    },
                ],
                "values": [
                    {"sourceColumnId": "col_1", "mode": "split", "timeMode": "split"}
                ],
                "orderColumnIds": ["col_4"],
            },
        },
    }


def test_resolution_daily_ranges_map_to_fiscal_weeks_and_configured_measure_aggregation(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(client)
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["rowCount"] == 14
    qty = next(i for i, c in enumerate(data["columns"]) if c["columnId"] == "col_1")
    date = next(
        i for i, c in enumerate(data["columns"]) if c.get("role") == "resolvedDate"
    )
    assert {r[qty] for r in data["rows"]} == {"10.0"}
    assert {r[date] for r in data["rows"]} == {f"2026-01-0{i}" for i in range(1, 8)}
    assert_post(client, "/transactions", tx)
    measure(client, "Total", "date_tx", "period_native", "col_1")
    measure(
        client,
        "Average",
        "date_tx",
        "period_native",
        "col_1",
        agg={
            "nonTime": "sum",
            "time": "avg",
            "nullInputPolicy": "ignore",
            "allNullResult": "null",
        },
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, cols = values(client, "period_native")
    assert [r[cols["Total"]] for r in result["rows"]] == [
        "30.0",
        "40.0",
        "30.0",
        "40.0",
    ]
    assert {r[cols["Average"]] for r in result["rows"]} == {"10.0"}


@pytest.mark.parametrize(
    "member_mode,time_mode,expected",
    [
        ("split", "split", "10.0"),
        ("copy", "split", "20.0"),
        ("split", "copy", "70.0"),
        ("copy", "copy", "140.0"),
    ],
)
def test_resolution_range_distribution_policies(
    authored_clickhouse_app, member_mode, time_mode, expected
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(client)
    tx["definition"]["resolution"]["values"][0].update(
        mode=member_mode, timeMode=time_mode
    )
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    data = result.json()
    qty = next(i for i, c in enumerate(data["columns"]) if c["columnId"] == "col_1")
    assert data["rowCount"] == 14
    assert {r[qty] for r in data["rows"]} == {expected}


def test_resolution_partial_week_overrides_preserve_other_days(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(
        client, b"140,2026-01-01,2026-01-07,10\n60,2026-01-02,2026-01-03,20\n"
    )
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    data = result.json()
    qty = next(i for i, c in enumerate(data["columns"]) if c["columnId"] == "col_1")
    date = next(
        i for i, c in enumerate(data["columns"]) if c.get("role") == "resolvedDate"
    )
    assert {(r[date], r[qty]) for r in data["rows"]} == {
        (f"2026-01-0{i}", "15.0" if i in (2, 3) else "10.0") for i in range(1, 8)
    }
    assert data["diagnostics"]["overwrittenMatches"] == 4
    assert_post(client, "/transactions", tx)
    measure(client, "Total", "date_tx", "period_native", "col_1")
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, cols = values(client, "period_native")
    assert {r[cols["Total"]] for r in result["rows"]} == {"40.0"}


@pytest.mark.parametrize(
    "rows,code",
    [
        (b"10,2026-01-05,2026-01-03,10\n", "invalid_resolution_range"),
        (b"10,not-a-date,2026-01-03,10\n", "invalid_resolution_range"),
        (b"10,,2026-01-03,10\n", "invalid_resolution_range"),
        (b"10,2025-12-31,2026-01-03,10\n", "resolution_calendar_coverage"),
        (
            b"140,2026-01-01,2026-01-07,10\n60,2026-01-02,2026-01-03,10\n",
            "ambiguous_resolution_order",
        ),
    ],
)
def test_resolution_range_rejects_invalid_dates_coverage_and_ties(
    authored_clickhouse_app, rows, code
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(client, rows)
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == code


def test_resolution_range_missing_native_week_is_not_clipped(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(
        client, population=b"product,period\nP1,FISCAL_A\nP1,FISCAL_B\nP2,FISCAL_A\n"
    )
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == "resolution_native_coverage"


def test_resolution_range_daily_native(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(client, day=True)
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert result.json()["rowCount"] == 14
    assert_post(client, "/transactions", tx)
    measure(client, "Daily", "date_tx", "period_native", "col_1")
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "period_native")
    assert {r[cols["Daily"]] for r in data["rows"]} == {"10.0"}


def test_resolution_range_first_last_uses_daily_calendar_order(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(
        client, b"140,2026-01-01,2026-01-07,10\n60,2026-01-02,2026-01-03,20\n"
    )
    assert_post(client, "/transactions", tx)
    for name, method in [("FirstDay", "first"), ("LastDay", "last")]:
        measure(
            client,
            name,
            "date_tx",
            "period_native",
            "col_1",
            agg={
                "nonTime": "sum",
                "time": method,
                "nullInputPolicy": "ignore",
                "allNullResult": "null",
            },
        )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "period_native")
    assert [r[cols["FirstDay"]] for r in data["rows"]] == ["10.0"] * 4
    assert [r[cols["LastDay"]] for r in data["rows"]] == [
        "15.0",
        "10.0",
        "15.0",
        "10.0",
    ]


def calendar_stage(client, tx):
    cfg = {
        "memberCatalogId": "Time",
        "startColumnId": "col_2",
        "endColumnId": "col_3",
        "selectedColumnIds": ["week"],
    }
    payload = {
        "transactionId": tx["objectId"],
        "sourceId": tx["definition"]["sourceId"],
        "calendarResolution": cfg,
    }
    schema = client.post(BASE + "/transactions/calendar/schema", json=payload)
    assert schema.status_code == 200, schema.text
    week = next(
        c["columnId"]
        for c in schema.json()["columns"]
        if c.get("memberColumnId") == "week"
    )
    tx["definition"]["calendarResolution"] = cfg
    tx["definition"]["resolution"]["axes"][1] = {
        "grainColumnId": "period",
        "rules": [
            {
                "ruleId": "calendar_week",
                "kind": "column",
                "sourceColumnId": week,
                "target": {"columnId": "period"},
            }
        ],
    }
    return payload


def test_calendar_stage_previews_before_native_mapping_and_materialises(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(client)
    payload = calendar_stage(client, tx)
    result = client.post(BASE + "/transactions/calendar/preview", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["rowCount"] == 7
    assert len(result.json()["rows"][0]) == len(result.json()["columns"])
    # The independent stage copies values; distribution happens only downstream.
    assert {r[0] for r in result.json()["rows"]} == {"140"}
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert result.json()["rowCount"] == 14
    assert len(result.json()["rows"][0]) == len(result.json()["columns"])
    assert_post(client, "/transactions", tx)
    measure(client, "Total", "date_tx", "period_native", "col_1")
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, cols = values(client, "period_native")
    assert [r[cols["Total"]] for r in result["rows"]] == [
        "30.0",
        "40.0",
        "30.0",
        "40.0",
    ]


def test_calendar_stage_overlaps_preserve_daily_last_assignment(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(
        client, b"140,2026-01-01,2026-01-07,10\n30,2026-01-02,2026-01-03,20\n"
    )
    calendar_stage(client, tx)
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    rows = result.json()
    day = next(
        i for i, c in enumerate(rows["columns"]) if c.get("role") == "resolvedDate"
    )
    qty = next(i for i, c in enumerate(rows["columns"]) if c["columnId"] == "col_1")
    assert {(r[day], r[qty]) for r in rows["rows"]} == {
        (f"2026-01-0{i}", "7.5" if i in (2, 3) else "10.0") for i in range(1, 8)
    }


@pytest.mark.parametrize("direct", [False, True])
def test_calendar_stage_week_month_cross_boundary(authored_clickhouse_app, direct):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(
        client,
        b"100,2026-01-30,2026-02-02,10\n",
        month=True,
        calendar_rows=b"day,week,month\n2026-01-29,W1,JAN\n2026-01-30,W1,JAN\n2026-01-31,W1,JAN\n2026-02-01,W1,FEB\n2026-02-02,W1,FEB\n2026-02-03,W2,FEB\n",
        population=b"product,period,month\nP1,W1,JAN\nP1,W1,FEB\nP1,W2,FEB\nP2,W1,JAN\nP2,W1,FEB\nP2,W2,FEB\n",
    )
    cfg = {
        "memberCatalogId": "Time",
        "startColumnId": "col_2",
        "endColumnId": "col_3",
        "selectedColumnIds": ["week", "month"],
    }
    tx["definition"]["calendarResolution"] = cfg
    result = client.post(
        BASE + "/transactions/calendar/schema",
        json={
            "transactionId": tx["objectId"],
            "sourceId": tx["definition"]["sourceId"],
            "calendarResolution": cfg,
        },
    )
    assert result.status_code == 200, result.text
    cols = {c.get("memberColumnId"): c["columnId"] for c in result.json()["columns"]}
    rules = [
        {
            "grainColumnId": k,
            "rules": [
                {
                    "ruleId": "map_" + k,
                    "sourceColumnId": cols[c],
                    "target": {"columnId": k},
                }
            ],
        }
        for k, c in [("period", "week"), ("month", "month")]
    ]
    tx["definition"]["resolution"]["axes"] = [
        tx["definition"]["resolution"]["axes"][0],
        *rules,
    ]
    tx["definition"]["resolution"]["values"][0].update(mode="copy", timeMode="copy")
    if direct:
        tx["definition"]["mappingMode"] = "direct"
        tx["definition"]["resolution"] = None
        new_source = managed(
            client,
            "DirectDates",
            b"product,start_date,end_date,quantity\nP1,2026-01-30,2026-02-02,100\n",
        )
        tx["definition"]["sourceId"] = new_source
        tx["definition"]["keyMappings"] = [
            {"sourceColumnId": "col_1", "grainColumnId": "product"},
            *[
                {"sourceColumnId": cols[c], "grainColumnId": k}
                for k, c in [("period", "week"), ("month", "month")]
            ],
        ]
        value_col = "col_4"
    else:
        value_col = "col_1"
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert_post(client, "/transactions", tx)
    measure(client, "Total", "date_tx", "period_native", value_col)
    measure(
        client,
        "Average",
        "date_tx",
        "period_native",
        value_col,
        agg={
            "nonTime": "sum",
            "time": "avg",
            "nullInputPolicy": "ignore",
            "allNullResult": "null",
        },
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, cols = values(client, "period_native")
    actual = [(r[cols["Total"]], r[cols["Average"]]) for r in result["rows"]]
    assert actual.count(("200.0", "100.0")) == (2 if direct else 4)


@pytest.mark.parametrize(
    "rows,code",
    [
        (b"10,2026-01-05,2026-01-03,10\n", "invalid_resolution_range"),
        (b"10,not-a-date,2026-01-03,10\n", "invalid_resolution_range"),
        (b"10,2025-12-31,2026-01-03,10\n", "resolution_calendar_coverage"),
    ],
)
def test_calendar_stage_rejects_bad_ranges_before_mapping(
    authored_clickhouse_app, rows, code
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(client, rows)
    payload = calendar_stage(client, tx)
    result = client.post(BASE + "/transactions/calendar/preview", json=payload)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == code


def test_calendar_stage_sparse_calendar_counts_only_eligible_days(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(
        client,
        calendar_rows=b"day,week\n2026-01-01,FISCAL_A\n2026-01-03,FISCAL_A\n2026-01-05,FISCAL_B\n2026-01-07,FISCAL_B\n",
    )
    calendar_stage(client, tx)
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    data = result.json()
    qty = next(i for i, c in enumerate(data["columns"]) if c["columnId"] == "col_1")
    assert data["rowCount"] == 8
    assert {r[qty] for r in data["rows"]} == {"17.5"}


def test_calendar_stage_direct_preserves_duplicate_records_and_aggregation_order(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_date_range(client)
    calendar_stage(client, tx)
    week = tx["definition"]["resolution"]["axes"][1]["rules"][0]["sourceColumnId"]
    source = managed(
        client,
        "DirectSeries",
        b"product,start_date,end_date,quantity\nP1,2026-01-01,2026-01-03,100\nP1,2026-01-02,2026-01-03,20\nP1,2026-01-02,2026-01-03,20\n",
    )
    tx["definition"].update(
        sourceId=source,
        mappingMode="direct",
        resolution=None,
        keyMappings=[
            {"sourceColumnId": "col_1", "grainColumnId": "product"},
            {"sourceColumnId": week, "grainColumnId": "period"},
        ],
    )
    assert_post(client, "/transactions", tx)
    for name, order in [
        ("NonTimeFirst", "nonTimeThenTime"),
        ("TimeFirst", "timeThenNonTime"),
    ]:
        measure(
            client,
            name,
            "date_tx",
            "period_native",
            "col_4",
            agg={
                "nonTime": "avg",
                "time": "sum",
                "order": order,
                "nullInputPolicy": "ignore",
                "allNullResult": "null",
            },
        )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, cols = values(client, "period_native")
    assert result["rows"][0][cols["NonTimeFirst"]] == "193.333333"
    assert result["rows"][0][cols["TimeFirst"]] == "126.666667"


def test_resolution_retained_text_can_control_application_order(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    tx = setup_resolution(client, rows=b"brand,A,zone,N,100,10\nid,P1,id,L1,30,20\n")
    d = tx["definition"]["resolution"]
    # Level selector is retained text: 'id' sorts after 'brand', even though
    # the selected source sequence is no longer used for precedence.
    d["passthroughColumnIds"] = ["col_1"]
    d["orderColumnIds"] = ["col_1"]
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    data = result.json()
    qty = next(i for i, c in enumerate(data["columns"]) if c["columnId"] == "col_5")
    text = next(i for i, c in enumerate(data["columns"]) if c["columnId"] == "col_1")
    assert sorted((r[qty], r[text]) for r in data["rows"]) == [
        ("30.0", "id"),
        ("50.0", "brand"),
    ]


@pytest.mark.parametrize("build_native", [False, True])
def test_resolution_preview_preserves_logical_column_ids(
    authored_clickhouse_app, build_native
):
    client, admin, _, _ = authored_clickhouse_app
    tx = setup_resolution(
        client,
        build_native=build_native,
        grain_names=("Product ID", "location_id"),
    )
    sql_response = client.post(
        BASE + "/transactions/resolution/preview?sql_only=true", json=tx
    )
    assert sql_response.status_code == 200, sql_response.text
    assert (
        admin.command("SELECT count() FROM (" + sql_response.json()["sql"] + ")") == 2
    )
    response = preview(client, tx)
    assert response.status_code == 200, response.text
    assert by_cell(response.json()) == {("P1", "L1"): "30.0", ("P2", "L1"): "50.0"}
    status = client.get(BASE + "/dimensions/native/table").json()
    assert status["state"] == "ready"
    assert status["storage"] == "virtual"
