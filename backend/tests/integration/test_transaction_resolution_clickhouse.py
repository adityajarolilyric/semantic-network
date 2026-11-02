# ruff: noqa: F811
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import assert_post, measure, build_values, values
from .test_planning_view_clickhouse import dimension

pytestmark = pytest.mark.integration


def setup_resolution(
    client, rows=b"brand,A,zone,N,100,10\nid,P1,id,L1,30,20\n", mode="split"
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
            "kind": "planningLevel",
            "name": "PL",
            "displayName": "PL",
            "definition": {
                "population": {"operation": "source", "sourceId": pop},
                "grain": {
                    "keyIds": ["product", "location"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"columnId": "col_1"},
                        },
                        {
                            "grainColumnId": "location",
                            "name": "location",
                            "populationRef": {"columnId": "col_2"},
                        },
                    ],
                },
                "dimensionBindings": [
                    {
                        "dimensionId": "Product",
                        "keyMappings": [
                            {"grainColumnId": "product", "dimensionColumnId": "product"}
                        ],
                        "selectedAttributeColumnIds": [],
                    },
                    {
                        "dimensionId": "Location",
                        "keyMappings": [
                            {
                                "grainColumnId": "location",
                                "dimensionColumnId": "location",
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
            json={"basePlanningLevelId": "native", "changeReason": ""},
            headers={"If-Match": str(rev["version"])},
        ).status_code
        == 200
    )
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
                {"value": literal, "target": {"dimensionId": dim, "columnId": col}}
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
            "planningLevelId": "native",
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
def test_resolution_split_copy_and_measure_loading(clickhouse_app, mode, expected):
    client, admin, database, _ = clickhouse_app
    tx = setup_resolution(client, mode=mode)
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "30.0", ("P2", "L1"): expected}
    assert result.json()["diagnostics"]["overwrittenMatches"] == 1
    assert client.get(BASE + "/transactions/mixed").status_code == 404
    assert_post(client, "/transactions", tx)
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
    assert count == 2


def test_resolution_later_coarse_wins_regardless_of_input_storage_order(clickhouse_app):
    client, _, _, _ = clickhouse_app
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
    clickhouse_app, rows, code
):
    client, _, _, _ = clickhouse_app
    tx = setup_resolution(client, rows)
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == code


def test_resolution_reduces_same_constraints_without_changing_split_denominator(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    tx = setup_resolution(
        client, b"brand,A,zone,N,100,10\nbrand,A,zone,N,200,20\nid,P1,id,L1,30,30\n"
    )
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "30.0", ("P2", "L1"): "100.0"}
    assert result.json()["diagnostics"]["shadowedAssignments"] == 1


def test_resolution_null_winner_is_not_filled_from_earlier_row(clickhouse_app):
    client, _, _, _ = clickhouse_app
    tx = setup_resolution(client, b"brand,A,zone,N,100,10\nid,P1,id,L1,,20\n")
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): None, ("P2", "L1"): "50.0"}


def test_resolution_expansion_limit_is_not_silent_truncation(clickhouse_app):
    client, _, _, settings = clickhouse_app
    tx = setup_resolution(client)
    settings.resolution_max_rows = 1
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == "resolution_expansion_limit"


def test_resolution_fixed_columns_and_explicit_all_members(clickhouse_app):
    client, _, _, _ = clickhouse_app
    tx = setup_resolution(client, b"id,P1,id,L1,60,10\n")
    axes = tx["definition"]["resolution"]["axes"]
    axes[0]["rules"] = [
        {
            "ruleId": "fixed",
            "kind": "column",
            "sourceColumnId": "col_2",
            "target": {"dimensionId": "Product", "columnId": "product"},
        }
    ]
    axes[1]["rules"] = []
    axes[1]["allowAll"] = True
    result = preview(client, tx)
    assert result.status_code == 200, result.text
    assert by_cell(result.json()) == {("P1", "L1"): "60.0"}


def test_resolution_string_tie_breaker_and_independent_partitions(clickhouse_app):
    client, _, _, _ = clickhouse_app
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
    client, rows=b"140,2026-01-01,2026-01-07,10\n", *, day=False, population=None
):
    calendar = managed(
        client,
        "DailyCalendar",
        b"day,week\n2026-01-01,FISCAL_A\n2026-01-02,FISCAL_A\n2026-01-03,FISCAL_A\n2026-01-04,FISCAL_B\n2026-01-05,FISCAL_B\n2026-01-06,FISCAL_B\n2026-01-07,FISCAL_B\n",
    )
    assert_post(
        client,
        "/dimensions",
        {
            "objectId": "Time",
            "kind": "dimension",
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
            "kind": "planningLevel",
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
                "dimensionBindings": [
                    {
                        "dimensionId": "Time",
                        "keyMappings": [
                            {
                                "grainColumnId": "period",
                                "dimensionColumnId": "day" if day else "week",
                            }
                        ],
                        "selectedAttributeColumnIds": [],
                    }
                ],
            },
        },
    )
    rev = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"basePlanningLevelId": "period_native", "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert response.status_code == 200, response.text
    assert build(client, "period_native")["state"] == "succeeded"
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
            "planningLevelId": "period_native",
            "mappingMode": "resolve_multi_grain",
            "keyMappings": [],
            "resolution": {
                "axes": [
                    {"grainColumnId": "product", "allowAll": True, "rules": []},
                    {
                        "grainColumnId": "period",
                        "dateRange": {
                            "dimensionId": "Time",
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
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
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
    clickhouse_app, member_mode, time_mode, expected
):
    client, _, _, _ = clickhouse_app
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


def test_resolution_partial_week_overrides_preserve_other_days(clickhouse_app):
    client, _, _, _ = clickhouse_app
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
    clickhouse_app, rows, code
):
    client, _, _, _ = clickhouse_app
    tx = setup_date_range(client, rows)
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == code


def test_resolution_range_missing_native_week_is_not_clipped(clickhouse_app):
    client, _, _, _ = clickhouse_app
    tx = setup_date_range(
        client, population=b"product,period\nP1,FISCAL_A\nP1,FISCAL_B\nP2,FISCAL_A\n"
    )
    result = preview(client, tx)
    assert result.status_code == 422, result.text
    assert result.json()["error"]["code"] == "resolution_native_coverage"


def test_resolution_range_daily_native(clickhouse_app):
    client, _, _, _ = clickhouse_app
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


def test_resolution_range_first_last_uses_daily_calendar_order(clickhouse_app):
    client, _, _, _ = clickhouse_app
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
