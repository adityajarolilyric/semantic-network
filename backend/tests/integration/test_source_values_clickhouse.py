"""Standalone measure values use the source, never a second permanent grain table."""

# ruff: noqa: F811
from uuid import uuid4
import time

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_measures_clickhouse import BASE, AGG, assert_post, build_values
from .test_planning_clickhouse import managed
from .test_managed_clickhouse import await_run
from .test_measure_edits_clickhouse import edit

pytestmark = pytest.mark.integration


def assert_physical_tables(admin, database):
    deadline = time.monotonic() + 5
    while True:
        tables = admin.query(
            "SELECT name FROM system.tables WHERE database={db:String} AND engine='MergeTree'",
            parameters={"db": database},
        ).result_rows
        if len(tables) == 1:
            return
        if time.monotonic() >= deadline:
            assert len(tables) == 1, tables
        time.sleep(0.02)


def seed(
    client,
    data=b"product,qty,other\nP1,10,7\nP1,20,8\nP2,30,9\n",
    allocation="weighted_by_self",
    aggregation="sum",
):
    client.app.state.container.warehouse_sources.upload_metadata = None
    source = managed(client, "IndependentForecast", data)
    transaction = assert_post(
        client,
        "/transactions",
        {
            "kind": "transaction",
            "objectId": "forecast",
            "name": "Forecast",
            "displayName": "Forecast",
            "definition": {"sourceId": source, "grainColumnIds": ["col_1"]},
        },
    )
    for name, column, editable in [("Qty", "col_2", True), ("Other", "col_3", False)]:
        assert_post(
            client,
            "/measures",
            {
                "kind": "measure",
                "objectId": name,
                "name": name,
                "displayName": name,
                "business": {},
                "definition": {
                    "measureType": "referenced",
                    "transactionId": "forecast",
                    "valueColumnId": column,
                    "editable": editable,
                    "aggregation": {
                        **AGG,
                        "nonTime": aggregation if name == "Qty" else "sum",
                    },
                    "allocation": {"mode": allocation},
                },
            },
        )
    return source, transaction["effectiveGrainId"]


def output(client, grain):
    response = client.get(
        BASE + "/measures/execution/levels/" + grain + "?preview=true"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_standalone_build_uses_source_and_edits_it_without_permanent_grain_table(
    clickhouse_app,
):
    client, admin, database, _ = clickhouse_app
    source, grain = seed(client)
    run = build_values(client)
    assert run["state"] == "succeeded", run
    assert_physical_tables(admin, database)
    assert admin.command(f"SELECT sum(qty) FROM `{database}`.IndependentForecast") == 60
    response = edit(
        client,
        mid="Qty",
        value=60.5,
        rows=[{"transactionId": "forecast", "columnId": "col_1"}],
        members=["P1"],
    )
    run = await_run(client, BASE, response)
    assert run["state"] == "succeeded", run
    assert float(
        admin.command(
            f"SELECT sum(qty) FROM `{database}`.IndependentForecast WHERE product='P1'"
        )
    ) == pytest.approx(60.5, abs=1e-6)
    assert admin.query(
        f"SELECT other FROM `{database}`.IndependentForecast ORDER BY other"
    ).result_rows == [(7,), (8,), (9,)]
    assert_physical_tables(admin, database)
    assert admin.command(f"SELECT count() FROM `{database}`.IndependentForecast") == 3
    stored_source = client.get(BASE + "/sources/" + source).json()
    assert stored_source["definition"]["columns"][1]["physicalType"] == "Float64"
    assert stored_source["definition"]["columns"][1]["nullable"]
    preview = output(client, grain)
    columns = {column["name"]: i for i, column in enumerate(preview["columns"])}
    assert float(preview["rows"][0][columns["Qty"]]) == pytest.approx(60.5, abs=1e-6)
    # Full Refresh reads the edited source of truth, rather than resurrecting an original copy.
    run = build_values(client)
    assert run["state"] == "succeeded", run
    assert float(output(client, grain)["rows"][0][columns["Qty"]]) == pytest.approx(
        60.5, abs=1e-6
    )


def build_incremental(client):
    revision = client.get(BASE).json()
    return await_run(
        client,
        BASE,
        client.post(
            BASE + "/measures/execution/build",
            json={"mode": "incremental"},
            headers={
                "If-Match": str(revision["version"]),
                "Idempotency-Key": uuid4().hex,
            },
        ),
    )


def test_derived_values_and_incremental_rebuild_read_the_edited_source(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    _, grain = seed(client)
    assert build_values(client)["state"] == "succeeded"
    assert build_incremental(client)["state"] == "succeeded"
    assert_post(
        client,
        "/measures",
        {
            "kind": "measure",
            "objectId": "Double",
            "name": "Double",
            "displayName": "Double",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "outputGrain": {"kind": "transaction", "objectId": "forecast"},
                "calculationType": "CalculateFirst",
                "aggregation": AGG,
                "formula": {"displayText": "[Qty]*2", "languageVersion": "planning-v1"},
            },
        },
    )
    assert build_incremental(client)["state"] == "succeeded"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=90,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "succeeded", run
    data = output(client, grain)
    columns = {column["name"]: i for i, column in enumerate(data["columns"])}
    assert float(data["rows"][0][columns["Double"]]) == 180
    assert build_incremental(client)["state"] == "succeeded"
    assert (
        float(
            admin.command(
                f"SELECT sum(qty) FROM `{database}`.IndependentForecast WHERE product='P1'"
            )
        )
        == 90
    )
    assert_physical_tables(admin, database)


@pytest.mark.parametrize(
    "aggregation,allocation,expected",
    [
        ("sum", "equal", 80),
        ("avg", "none", 80),
        ("avg", "weighted_by_self", 80),
        ("min", "none", 80),
        ("max", "none", 80),
    ],
)
def test_source_rows_use_configured_aggregation_and_allocation(
    clickhouse_app, aggregation, allocation, expected
):
    client, admin, database, _ = clickhouse_app
    _, grain = seed(client, allocation=allocation, aggregation=aggregation)
    assert build_values(client)["state"] == "succeeded"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=80,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "succeeded", run
    data = output(client, grain)
    columns = {column["name"]: i for i, column in enumerate(data["columns"])}
    assert float(data["rows"][0][columns["Qty"]]) == expected
    assert admin.command(f"SELECT count() FROM `{database}`.IndependentForecast") == 3


def test_failed_source_publication_rolls_back_rows_schema_and_views(
    clickhouse_app, monkeypatch
):
    client, admin, database, _ = clickhouse_app
    source, grain = seed(client)
    assert build_values(client)["state"] == "succeeded"
    original = admin.query(
        f"SELECT * FROM `{database}`.IndependentForecast ORDER BY ALL"
    ).result_rows
    before = client.get(BASE + "/sources/" + source).json()
    activation = client.app.state.container.measure_builds.handler.activation
    original_apply = activation.source_schema.apply

    async def fail(*args, **kwargs):
        await original_apply(*args, **kwargs)
        raise RuntimeError("test metadata activation failure")

    monkeypatch.setattr(activation.source_schema, "apply", fail)
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=95.5,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "failed", run
    assert (
        admin.query(
            f"SELECT * FROM `{database}`.IndependentForecast ORDER BY ALL"
        ).result_rows
        == original
    )
    assert client.get(BASE + "/sources/" + source).json() == before
    assert output(client, grain)["state"] == "ready"
    assert_physical_tables(admin, database)


def test_noop_edit_keeps_the_source_uuid_and_type(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    _, grain = seed(client)
    assert build_values(client)["state"] == "succeeded"
    before = admin.query(
        "SELECT toString(uuid),create_table_query FROM system.tables WHERE database={db:String} AND name='IndependentForecast'",
        parameters={"db": database},
    ).first_row
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=30,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "succeeded", run
    after = admin.query(
        "SELECT toString(uuid),create_table_query FROM system.tables WHERE database={db:String} AND name='IndependentForecast'",
        parameters={"db": database},
    ).first_row
    assert before == after
    assert output(client, grain)["state"] == "ready"


def test_external_insert_during_source_edit_is_not_overwritten(
    clickhouse_app, monkeypatch
):
    client, admin, database, _ = clickhouse_app
    _, grain = seed(client)
    assert build_values(client)["state"] == "succeeded"
    storage = client.app.state.container.measure_builds.storage.source_values
    publish = storage.publish

    async def insert_then_publish(artifact):
        admin.command(
            f"INSERT INTO `{database}`.IndependentForecast VALUES ('P3',44,12)"
        )
        return await publish(artifact)

    monkeypatch.setattr(storage, "publish", insert_then_publish)
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=70,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "superseded", run
    assert "source data or schema changed" in str(run).lower()
    assert admin.command(f"SELECT count() FROM `{database}`.IndependentForecast") == 4
    assert (
        admin.command(
            f"SELECT sum(qty) FROM `{database}`.IndependentForecast WHERE product='P1'"
        )
        == 30
    )
    assert output(client, grain)["rowCount"] == 3


def test_full_refresh_retires_the_old_engine_copy_when_adopting_source_storage(
    clickhouse_app, monkeypatch
):
    from backend.app.engine.graph.source_storage import SourceStoragePolicy

    client, admin, database, _ = clickhouse_app
    _, grain = seed(client)
    policy = SourceStoragePolicy.binding
    monkeypatch.setattr(SourceStoragePolicy, "binding", lambda *_: None)
    assert build_values(client)["state"] == "succeeded"
    assert (
        int(
            admin.command(
                "SELECT count() FROM system.tables WHERE database={db:String} AND engine='MergeTree'",
                parameters={"db": database},
            )
        )
        == 2
    )
    monkeypatch.setattr(SourceStoragePolicy, "binding", policy)
    incremental = build_incremental(client)
    assert incremental["state"] == "superseded", incremental
    assert "Full Semantic Refresh" in str(incremental)
    assert build_values(client)["state"] == "succeeded"
    assert_physical_tables(admin, database)
    assert output(client, grain)["state"] == "ready"


def test_converted_reference_writes_source_units_and_refreshes_aliases(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    _, grain = seed(client, data=b"product,qty,other\nP1,10,2\nP1,20,2\nP2,30,3\n")
    assert_post(
        client,
        "/measures",
        {
            "objectId": "Factor",
            "kind": "measure",
            "name": "Factor",
            "displayName": "Factor",
            "business": {},
            "definition": {
                "measureType": "referenced",
                "transactionId": "forecast",
                "valueColumnId": "col_3",
                "isConversionFactor": True,
                "aggregation": AGG,
            },
        },
    )
    assert_post(
        client,
        "/measures",
        {
            "objectId": "Converted",
            "kind": "measure",
            "name": "Converted",
            "displayName": "Converted",
            "business": {},
            "definition": {
                "measureType": "referenced",
                "transactionId": "forecast",
                "valueColumnId": "col_2",
                "editable": True,
                "aggregation": AGG,
                "conversion": {
                    "factorMeasureId": "Factor",
                    "operation": "multiply",
                    "targetUnit": "EA",
                },
            },
        },
    )
    assert build_values(client)["state"] == "succeeded"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Converted",
            value=120,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "succeeded", run
    assert (
        float(
            admin.command(
                f"SELECT sum(qty) FROM `{database}`.IndependentForecast WHERE product='P1'"
            )
        )
        == 60
    )
    data = output(client, grain)
    columns = {column["name"]: i for i, column in enumerate(data["columns"])}
    assert float(data["rows"][0][columns["Qty"]]) == 60
    assert float(data["rows"][0][columns["Converted"]]) == 120
    assert_physical_tables(admin, database)


def test_source_self_weights_preserve_null_physical_rows(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    seed(client, data=b"product,qty,other\nP1,10,7\nP1,,8\nP1,20,9\nP2,5,1\n")
    assert build_values(client)["state"] == "succeeded"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=60,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "succeeded", run
    result = admin.query(
        f"SELECT qty FROM `{database}`.IndependentForecast WHERE product='P1' ORDER BY other"
    ).result_rows
    assert result == [(20.0,), (None,), (40.0,)]


def test_source_backed_count_is_read_only(clickhouse_app):
    client, *_ = clickhouse_app
    seed(client, aggregation="count")
    assert build_values(client)["state"] == "succeeded"
    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"transactionId": "forecast", "columnId": "col_1"}],
            "measures": ["Qty"],
        },
    )
    assert response.status_code == 200, response.text
    assert not response.json()["measures"][0]["editable"]
    assert "count" in response.json()["measures"][0]["editReason"].lower()


def test_invalid_dependent_value_rejects_source_edit_before_publication(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    _, grain = seed(client)
    assert_post(
        client,
        "/measures",
        {
            "objectId": "Ratio",
            "kind": "measure",
            "name": "Ratio",
            "displayName": "Ratio",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "outputGrain": {"kind": "transaction", "objectId": "forecast"},
                "calculationType": "CalculateFirst",
                "aggregation": AGG,
                "resultType": {"type": "number", "nullable": False},
                "formula": {
                    "displayText": "1 / ([Qty] - 60)",
                    "languageVersion": "planning-v1",
                },
            },
        },
    )
    assert build_values(client)["state"] == "succeeded"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=60,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "failed", run
    assert "non-nullable" in str(run).lower()
    assert (
        admin.command(
            f"SELECT sum(qty) FROM `{database}`.IndependentForecast WHERE product='P1'"
        )
        == 30
    )
    assert output(client, grain)["state"] == "ready"
    assert_physical_tables(admin, database)


def test_unrepresentable_source_weights_leave_original_values_unchanged(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    _, grain = seed(client, aggregation="min")
    assert build_values(client)["state"] == "succeeded"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="Qty",
            value=-10,
            rows=[{"transactionId": "forecast", "columnId": "col_1"}],
            members=["P1"],
        ),
    )
    assert run["state"] == "failed", run
    assert "cannot reproduce" in str(run)
    assert (
        admin.command(
            f"SELECT sum(qty) FROM `{database}`.IndependentForecast WHERE product='P1'"
        )
        == 30
    )
    assert output(client, grain)["state"] == "ready"
    assert_physical_tables(admin, database)
