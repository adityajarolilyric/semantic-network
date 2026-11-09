"""Cell-level edit invalidation, including native-grain fanout and no-op edits."""
# ruff: noqa: F811

import time

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_decision_view_clickhouse import seed_view, field, query_view
from .test_measure_edits_clickhouse import editable, edit
from .test_measures_clickhouse import BASE, assert_post, build_values, values

pytestmark = pytest.mark.integration


def changed_rows(client, run):
    return {
        artifact["resourceId"]: {
            group["measureIds"][0]: group["updatedRows"]
            for group in artifact["buildTimingsMs"]["groups"]
        }
        for artifact in client.app.state.container.store.documents["engine_artifacts"]
        if artifact["runId"] == run["runId"]
        and artifact["resourceKind"] == "measureValues"
        and artifact["state"] == "ready"
    }


def cell_edit(client, value=20):
    response = edit(
        client,
        value=value,
        rows=[field("Product", "product"), field("Location", "location")],
        members=["P1", "L1"],
    )
    assert response.status_code == 202, response.text
    run = response.json()
    deadline = time.monotonic() + 60
    while run["state"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        run = client.get(BASE + "/engine/runs/" + run["runId"]).json()
    assert run["state"] == "succeeded", run
    return run


def test_one_cell_updates_only_its_dependants_and_coarser_groups(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    seed_view(client)
    editable(client, mode="none")
    before, _ = values(client, "base")
    run = cell_edit(client)
    rows = changed_rows(client, run)
    assert rows["base"] == {"FQTY": 1, "ErrorAF": 1, "ErrorCF": 1}
    assert rows["pt"] == {"FQTY": 1, "ErrorAF": 1, "ErrorCF": 1}
    # Lookup-only dimensions remain outside the materialisation target set.
    assert "brand_zone" not in rows
    after, columns = values(client, "base")
    assert after["rows"][1:] == before["rows"][1:]
    assert float(after["rows"][0][columns["FQTY"]]) == 20
    assert float(after["rows"][0][columns["ErrorCF"]]) == 40
    coarse, columns = values(client, "pt")
    assert (
        float(coarse["rows"][0][columns["FQTY"]]) == 50
    )  # include the untouched L2=30 contributor
    assert float(coarse["rows"][0][columns["ErrorCF"]]) == 70
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        stages = [
            artifact
            for artifact in client.app.state.container.store.documents[
                "engine_artifacts"
            ]
            if artifact["runId"] == run["runId"]
            and artifact["resourceKind"] == "editCells"
        ]
        if stages and all(stage["state"] == "dropped" for stage in stages):
            break
        time.sleep(0.02)
    assert stages and all(stage["state"] == "dropped" for stage in stages)
    assert (
        admin.command(
            "SELECT count() FROM system.tables WHERE database={db:String} AND name IN {names:Array(String)}",
            parameters={
                "db": database,
                "names": [stage["physicalName"] for stage in stages],
            },
        )
        == 0
    )


def test_same_six_place_value_does_not_recalculate_or_mutate_dependants(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client, mode="none")
    before, _ = values(client, "base")
    run = cell_edit(client, 10.0000001)
    assert all(
        count == 0
        for groups in changed_rows(client, run).values()
        for count in groups.values()
    )
    after, _ = values(client, "base")
    assert after["rows"] == before["rows"]


def test_calculate_first_native_group_expands_affected_finer_cells(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client, mode="none")
    assert_post(
        client,
        "/measures",
        {
            "objectId": "square",
            "formulaDefinition": "Square = [FQTY] * [FQTY]",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": "pt",
                "calculationType": "CalculateFirst",
                "aggregation": {"nonTime": "sum", "time": "sum"},
                "allocation": {"mode": "equal"},
            },
        },
    )
    assert_post(
        client,
        "/measures",
        {
            "objectId": "dependent",
            "formulaDefinition": "Dependent = [Square] + [FQTY]",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": "base",
                "calculationType": "AggregateFirst",
            },
        },
    )
    built = build_values(client)
    assert built["state"] == "succeeded", built
    before, _ = values(client, "base")
    run = cell_edit(client)
    rows = changed_rows(client, run)
    assert rows["base"]["FQTY"] == 1
    assert rows["base"]["square"] == rows["base"]["dependent"] == 2
    assert rows["pt"]["square"] == rows["pt"]["dependent"] == 1
    after, columns = values(client, "base")
    assert [float(row[columns["Square"]]) for row in after["rows"][:2]] == [1250, 1250]
    assert [float(row[columns["Dependent"]]) for row in after["rows"][:2]] == [
        1270,
        1280,
    ]
    assert after["rows"][2:] == before["rows"][2:]


def test_counts_recompute_all_contributors_in_the_affected_group(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client, mode="none")
    for oid, name, expression in [
        ("distinct_qty", "Distinct Qty", "COUNT_DISTINCT([FQTY])"),
        ("positive_qty", "Positive Qty", "COUNT_IF([FQTY] > 0)"),
    ]:
        assert_post(
            client,
            "/measures",
            {
                "objectId": oid,
                "formulaDefinition": name + " = " + expression,
                "business": {},
                "definition": {
                    "measureType": "calculated",
                    "nativeDimensionId": "base",
                    "calculationType": "AggregateFirst",
                },
            },
        )
    built = build_values(client)
    assert built["state"] == "succeeded", built
    cell_edit(client, 30)
    coarse, columns = values(client, "pt")
    assert float(coarse["rows"][0][columns["Distinct Qty"]]) == 1
    assert float(coarse["rows"][0][columns["Positive Qty"]]) == 2
    cell_edit(client, -5)
    coarse, columns = values(client, "pt")
    assert float(coarse["rows"][0][columns["Distinct Qty"]]) == 2
    assert float(coarse["rows"][0][columns["Positive Qty"]]) == 1


def test_scoped_edits_preserve_typed_values_and_recompute_mixed_groups(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client, mode="none")
    for oid, name, expression, aggregation in [
        ("status", "Status", 'IF([FQTY] > 0, "Positive", "Negative")', "single_value"),
        ("positive", "Positive", "[FQTY] > 0", "any_true"),
        (
            "next_date",
            "Next Date",
            'IF([FQTY] > 0, DATE("2026-11-09"), DATE("2026-11-10"))',
            "max",
        ),
    ]:
        assert_post(
            client,
            "/measures",
            {
                "objectId": oid,
                "formulaDefinition": name + " = " + expression,
                "business": {},
                "definition": {
                    "measureType": "calculated",
                    "nativeDimensionId": "base",
                    "calculationType": "CalculateFirst",
                    "aggregation": {"nonTime": aggregation, "time": aggregation},
                },
            },
        )
    built = build_values(client)
    assert built["state"] == "succeeded", built
    before, _ = values(client, "base")
    run = cell_edit(client, -5)
    assert all(
        changed_rows(client, run)["base"][mid] == 1
        for mid in ["status", "positive", "next_date"]
    )
    after, columns = values(client, "base")
    assert after["rows"][0][columns["Status"]] == "Negative"
    assert after["rows"][0][columns["Positive"]].lower() in {"false", "0"}
    assert after["rows"][0][columns["Next Date"]] == "2026-11-10"
    assert after["rows"][1:] == before["rows"][1:]
    response = query_view(
        client,
        rows=[field("Product", "product")],
        measures=["status", "positive", "next_date"],
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["cellStates"][0][0] == "mixed"
    assert result["rows"][0][3] == "2026-11-10"
