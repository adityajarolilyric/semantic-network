"""Native Week–Month windows and scoped edits across calendar boundaries."""
# ruff: noqa: F811

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import (
    transaction,
    measure,
    assert_post,
    build_values,
    values,
)
from .test_temporal_conversions_clickhouse import calculated
from .test_measure_edits_clickhouse import edit
from .test_managed_clickhouse import await_run
from .test_edit_cell_scopes_clickhouse import changed_rows

pytestmark = pytest.mark.integration


def seed_slices(client):
    client.app.state.container.warehouse_sources.upload_metadata = None
    calendar = managed(
        client,
        "CalendarDays",
        b"date,week,month,week_start\n2026-12-28,W53-26,2026-12,2026-12-28\n2026-12-31,W53-26,2026-12,2026-12-28\n2027-01-01,W53-26,2027-01,2026-12-28\n2027-01-03,W53-26,2027-01,2026-12-28\n2027-01-04,W1-27,2027-01,2027-01-04\n2027-01-10,W1-27,2027-01,2027-01-04\n2027-01-18,W3-27,2027-01,2027-01-18\n",
    )
    assert_post(
        client,
        "/dimension-members",
        {
            "objectId": "Time",
            "kind": "dimensionMembers",
            "name": "Time",
            "displayName": "Time",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar,
                "columns": [
                    {
                        "columnId": name,
                        "name": name,
                        "displayName": name,
                        "sourceColumnId": f"col_{index}",
                    }
                    for index, name in enumerate(
                        ["date", "week", "month", "week_start"], 1
                    )
                ],
                "keyColumnIds": ["date"],
                "attributeColumnIds": ["date", "week", "month", "week_start"],
                "labelColumnId": "date",
                "time": {
                    "baseGrain": "day",
                    "dateColumnId": "date",
                    "orderingColumnId": "date",
                },
            },
        },
    )
    facts = managed(
        client,
        "DemandSlices",
        b"product,location,week,month,qty\nP1,L1,W53-26,2026-12,10\nP1,L1,W53-26,2027-01,20\nP1,L1,W1-27,2027-01,40\nP1,L1,W3-27,2027-01,80\nP2,L1,W53-26,2026-12,100\nP2,L1,W53-26,2027-01,200\nP2,L1,W1-27,2027-01,400\nP2,L1,W3-27,2027-01,800\n",
    )
    keys = ["product", "location", "week", "month"]
    create(
        client,
        {
            "objectId": "base",
            "kind": "dimension",
            "name": "ProdLocWeekMonth",
            "displayName": "ProdLocWeekMonth",
            "definition": {
                "population": {"operation": "source", "sourceId": facts},
                "grain": {
                    "keyIds": keys,
                    "columns": [
                        {
                            "grainColumnId": name,
                            "name": name,
                            "populationRef": {"columnId": f"col_{index}"},
                        }
                        for index, name in enumerate(keys, 1)
                    ],
                },
                "memberBindings": [
                    {
                        "memberCatalogId": "Time",
                        "keyMappings": [
                            {"grainColumnId": key, "memberColumnId": key}
                            for key in ["week", "month"]
                        ],
                        "selectedAttributeColumnIds": ["week_start"],
                    }
                ],
            },
        },
    )
    revision = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"baseDimensionId": "base", "changeReason": ""},
        headers={"If-Match": str(revision["version"])},
    )
    assert response.status_code == 200, response.text
    transaction(
        client,
        "demand",
        facts,
        "base",
        [(key, f"col_{i}") for i, key in enumerate(keys, 1)],
    )
    measure(client, "Qty", "demand", "base", "col_5")
    current = client.get(BASE + "/measures/Qty").json()
    payload = {
        key: value
        for key, value in current.items()
        if key not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    payload["definition"]["editable"] = True
    response = client.put(
        BASE + "/measures/Qty",
        json=payload,
        headers={"If-Match": str(current["version"])},
    )
    assert response.status_code == 200, response.text
    assert build(client, "base")["state"] == "succeeded"
    return keys


def rows_by_key(client):
    data, columns = values(client, "base")
    return {
        (row[columns["product"]], row[columns["week"]], row[columns["month"]]): {
            name: None if row[index] is None else float(row[index])
            for name, index in columns.items()
            if name in {"Qty", "Running", "Rolling", "Minimum", "Average"}
        }
        for row in data["rows"]
    }


def test_calendar_slices_materialise_and_edit_only_affected_intersections(
    clickhouse_app,
):
    client, *_ = clickhouse_app
    keys = seed_slices(client)
    context = None
    for name, text in [
        ("Running", 'CUMULATIVE([Qty],"SUM")'),
        ("Rolling", 'ROLLING([Qty],"SUM",-1,0)'),
    ]:
        response = calculated(client, name, text, context=None)
        assert response.status_code == 201, response.text
        context = response.json()["definition"]["temporal"]
        assert context["partitionColumnIds"] == ["product", "location"]
    response = calculated(
        client, "Minimum", 'ROLLING([Qty],"SUM",-1,0)', {**context, "minimumPeriods": 2}
    )
    assert response.status_code == 201, response.text
    run = build_values(client)
    assert run["state"] == "succeeded", run
    before = rows_by_key(client)
    ordered = [
        ("W53-26", "2026-12"),
        ("W53-26", "2027-01"),
        ("W1-27", "2027-01"),
        ("W3-27", "2027-01"),
    ]
    assert [before["P1", week, month]["Running"] for week, month in ordered] == [
        10,
        30,
        70,
        150,
    ]
    assert [before["P1", week, month]["Rolling"] for week, month in ordered] == [
        30,
        30,
        70,
        80,
    ]
    assert [before["P1", week, month]["Minimum"] for week, month in ordered] == [
        None,
        None,
        70,
        None,
    ]
    assert [before["P2", week, month]["Running"] for week, month in ordered] == [
        100,
        300,
        700,
        1500,
    ]
    # Multi-window edit overlays run with the deployed Compose memory budget.
    # Initial materialisation and the separate AVG regression retain 512 MiB.
    client.app.state.container.settings.planning_max_bytes = 2_147_483_648
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=25,
            rows=[{"dimensionId": "base", "columnId": key} for key in keys],
            members=["P1", "L1", "W53-26", "2027-01"],
        ),
    )
    assert run["state"] == "succeeded", run
    after = rows_by_key(client)
    assert [after["P1", week, month]["Running"] for week, month in ordered] == [
        10,
        35,
        75,
        155,
    ]
    assert [after["P1", week, month]["Rolling"] for week, month in ordered] == [
        35,
        35,
        75,
        80,
    ]
    assert all(after[key] == value for key, value in before.items() if key[0] == "P2")
    counts = changed_rows(client, run)["base"]
    assert counts["Running"] == 3
    assert counts["Rolling"] == 3
    assert counts["Qty"] == 1


def test_rolling_slice_operation_is_not_assumed_to_be_sum(clickhouse_app):
    client, *_ = clickhouse_app
    seed_slices(client)
    response = calculated(client, "Average", 'ROLLING([Qty],"AVG",-1,0)', context=None)
    assert response.status_code == 201, response.text
    run = build_values(client)
    assert run["state"] == "succeeded", run
    rows = rows_by_key(client)
    assert rows["P1", "W53-26", "2026-12"]["Average"] == 15
    assert rows["P1", "W53-26", "2027-01"]["Average"] == 15
    assert rows["P1", "W1-27", "2027-01"]["Average"] == 23.333333


def test_lag_does_not_pick_an_arbitrary_row_from_a_split_week(clickhouse_app):
    client, *_ = clickhouse_app
    seed_slices(client)
    response = calculated(client, "PreviousWeek", "LAG([Qty],1)", context=None)
    assert response.status_code == 201, response.text
    run = build_values(client)
    assert run["state"] == "failed", run
    assert run["error"]["code"] == "temporal_order_conflict"
