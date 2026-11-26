# ruff: noqa: F811
from datetime import timedelta

import pytest

from backend.app.core.errors import MetadataError
from backend.app.core.metadata import utc_now
from backend.app.warehouse.sql import quote_literal
from .test_csv_clickhouse import authored_clickhouse_app, clickhouse_app  # noqa: F401
from .test_incremental_clickhouse import calc, run_incremental, incremental
from .test_managed_clickhouse import await_run
from .test_measures_clickhouse import measure, transaction, values, update_measure
from .test_planning_clickhouse import BASE, create, managed, level
from .test_typed_measures_clickhouse import ref

pytestmark = pytest.mark.integration


def seed(client):
    source = managed(client, "Facts", b"product,quantity\nP1,40\nP2,60\n")
    create(client, level(source, "base", attribute=False))
    transaction(client, "facts", source, "base", [("product", "col_1")])
    measure(client, "FQTY", "facts", "base", "col_2")
    run = run_incremental(client, "full")
    assert run["state"] == "succeeded", run
    return run


def test_single_source_is_virtual_and_added_measure_updates_only_its_columns(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    container = client.app.state.container
    assert not [
        b
        for b in container.store.documents["engine_artifact_bindings"]
        if b.get("resourceKind") == "dimension"
    ]
    source_status = client.get(BASE + "/dimensions/base/table").json()
    assert source_status["storage"] == "virtual"
    prior = next(
        a
        for a in container.store.documents["engine_artifacts"]
        if a.get("resourceKind") == "measureValues" and a.get("state") == "ready"
    )
    count = admin.command(f"SELECT count() FROM `{database}`.`{prior['physicalName']}`")
    calc(client, "Double")
    commands = []
    original = type(admin).command

    def command(connection, cmd, *args, **kwargs):
        commands.append(cmd)
        return original(connection, cmd, *args, **kwargs)

    monkeypatch.setattr(type(admin), "command", command)
    built = run_incremental(client)
    assert built["state"] == "succeeded", built
    result, cols = values(client, "base")
    assert [r[cols["Double"]] for r in result["rows"]] == ["80.0", "120.0"]
    assert [r[cols["FQTY"]] for r in result["rows"]] == ["40.0", "60.0"]
    assert (
        admin.command(f"SELECT count() FROM `{database}`.`{prior['physicalName']}`")
        == count
    )
    assert any(" UPDATE " in c for c in commands)
    inserts = [c for c in commands if c.startswith("INSERT INTO")]
    assert inserts and all(
        any(suffix in c.split(" SELECT", 1)[0] for suffix in ("_lookup`", "_values`"))
        for c in inserts
    )
    updates = [c for c in commands if " UPDATE " in c]
    assert all("joinGet(" in c and "transform(" not in c for c in updates)
    step = next(
        s
        for s in container.store.documents["engine_run_steps"]
        if s["runId"] == built["measureRunId"] and s["stepId"] == "values_base"
    )
    assert step["writeMode"] == "in_place"
    assert step["updatedColumns"] == ["Double"]

    # Expired metadata aliases must not delete the shared active row generation.
    async def expire(repo):
        await repo.replace(
            "engine_artifacts",
            {"artifactId": prior["artifactId"]},
            {**prior, "createdAt": utc_now() - timedelta(days=2)},
        )

    client.portal.call(container.store.transaction, expire)
    client.portal.call(container.engine_worker.collector.collect)
    assert values(client, "base")[0]["rowCount"] == 2


def test_reads_block_and_failed_mutations_require_recovery(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    container = client.app.state.container
    client.portal.call(container.engine_worker.stop)
    calc(client, "Double")
    response = incremental(client)
    assert response.status_code == 202, response.text
    assert (
        client.get(BASE + "/measures/execution/levels/base?preview=true").status_code
        == 503
    )
    query = {"rows": [], "columns": [], "measures": ["FQTY"]}
    assert client.post(BASE + "/decision-view/query", json=query).status_code == 503
    writer = container.measure_builds.executor.in_place
    original = writer.build

    async def fail_after_write(*args, **kwargs):
        await original(*args, **kwargs)
        artifact = args[0]
        for suffix, definition in (
            ("values", "(x String) ENGINE=MergeTree ORDER BY tuple()"),
            ("lookup", "(__update_key String) ENGINE=Join(ANY, LEFT, __update_key)"),
        ):
            admin.command(
                f"CREATE TABLE `{database}`.`{artifact['physicalName']}_{suffix}` {definition} COMMENT "
                + quote_literal(artifact["buildOwner"] + ":" + suffix)
            )
        raise MetadataError("test_write_failure", "Injected after mutation")

    monkeypatch.setattr(writer, "build", fail_after_write)
    client.portal.call(container.engine_worker.start)
    failed = await_run(client, BASE, response)
    assert failed["state"] == "failed", failed
    assert (
        client.get(BASE + "/measures/execution/levels/base?preview=true").status_code
        == 503
    )
    monkeypatch.setattr(writer, "build", original)
    recovered = run_incremental(client)
    assert recovered["state"] == "succeeded", recovered
    assert recovered["buildMode"] == "incremental"
    assert values(client, "base")[0]["rowCount"] == 2

    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows


def test_column_checkpoints_preserve_typed_nulls_counts_and_dependency_order(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    source = managed(
        client,
        "Typed",
        b"product,quantity,status,dispatch,active\nP1,40,Healthy,2026-11-01,true\nP2,60,,,false\n",
    )
    create(client, level(source, "base", attribute=False))
    transaction(client, "facts", source, "base", [("product", "col_1")])
    measure(client, "FQTY", "facts", "base", "col_2")
    ref(client, "Status", "facts", "base", "col_3", "single_value")
    ref(client, "Dispatch", "facts", "base", "col_4", "max")
    ref(client, "Active", "facts", "base", "col_5", "any_true")
    assert run_incremental(client, "full")["state"] == "succeeded"
    for mid, text in [
        ("Flag", "[FQTY] > 50"),
        ("Label", 'CONCAT(COALESCE([Status], "Unknown"), "!")'),
        ("Later", "ADD_DAYS([Dispatch], 2)"),
        ("Count", "COUNT([FQTY])"),
    ]:
        response = client.post(
            BASE + "/measures",
            json={
                "objectId": mid,
                "name": mid,
                "displayName": mid,
                "business": {},
                "definition": {
                    "measureType": "calculated",
                    "outputGrain": {"kind": "dimension", "objectId": "base"},
                    "calculationType": "AggregateFirst",
                    "formula": {"displayText": text, "languageVersion": "planning-v1"},
                },
            },
        )
        assert response.status_code == 201, response.text
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    result, cols = values(client, "base")
    assert [
        [r[cols[m]] for m in ["Flag", "Label", "Later", "Count"]]
        for r in result["rows"]
    ] == [["false", "Healthy!", "2026-11-03", "1.0"], ["true", "Unknown!", None, "1.0"]]


def test_recovery_recomputes_touched_measures_even_after_metadata_is_reverted(
    authored_clickhouse_app, monkeypatch
):
    client, _, _, _ = authored_clickhouse_app
    seed(client)
    calc(client, "Double")
    assert run_incremental(client)["state"] == "succeeded"
    update_measure(
        client, "Double", lambda d: d["formula"]["expression"]["right"].update(value=3)
    )
    writer = client.app.state.container.measure_builds.executor.in_place
    original = writer.build

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise MetadataError("test_failure", "Fail after changed values were written")

    monkeypatch.setattr(writer, "build", fail)
    assert run_incremental(client)["state"] == "failed"
    update_measure(
        client, "Double", lambda d: d["formula"]["expression"]["right"].update(value=2)
    )
    monkeypatch.setattr(writer, "build", original)
    recovered = run_incremental(client)
    assert recovered["state"] == "succeeded", recovered
    assert not recovered.get("reused", False)
    data, cols = values(client, "base")
    assert [r[cols["Double"]] for r in data["rows"]] == ["80.0", "120.0"]


def test_cancelled_writer_stops_at_the_next_checkpoint(
    authored_clickhouse_app, monkeypatch
):
    client, _, _, _ = authored_clickhouse_app
    seed(client)
    calc(client, "Double")
    container = client.app.state.container
    storage = container.measure_builds.storage
    original = storage.require_running
    calls = 0

    async def guard(artifact):
        nonlocal calls
        calls += 1
        if calls == 4:
            child = await container.store.repository.get(
                "engine_runs", {"runId": artifact["runId"]}
            )
            await container.managed_tables.cancel(
                "default_model", "revision_1", child["networkRunId"]
            )
        await original(artifact)

    monkeypatch.setattr(storage, "require_running", guard)
    cancelled = run_incremental(client)
    assert cancelled["state"] == "cancelled", cancelled
    assert calls == 4
    assert (
        client.get(BASE + "/measures/execution/levels/base?preview=true").status_code
        == 503
    )
    monkeypatch.setattr(storage, "require_running", original)
    recovered = run_incremental(client)
    assert recovered["state"] == "succeeded", recovered
    data, cols = values(client, "base")
    assert [r[cols["Double"]] for r in data["rows"]] == ["80.0", "120.0"]


def test_standalone_transaction_builds_without_dimension_or_population_table(
    authored_clickhouse_app,
):
    client, _, _, _ = authored_clickhouse_app
    source = managed(client, "Standalone", b"product,quantity\nP1,40\nP2,60\n")
    created = client.post(
        BASE + "/transactions",
        json={
            "objectId": "facts",
            "name": "Facts",
            "displayName": "Facts",
            "definition": {"sourceId": source, "grainColumnIds": ["col_1"]},
            "measures": [
                {
                    "valueColumnId": "col_2",
                    "editable": False,
                    "aggregation": {"nonTime": "sum", "time": "sum"},
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    grain = client.get(BASE + "/transactions/facts").json()["effectiveGrainId"]
    assert run_incremental(client, "full")["state"] == "succeeded"
    response = client.post(
        BASE + "/measures",
        json={
            "objectId": "Double",
            "name": "Double",
            "displayName": "Double",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "outputGrain": {"kind": "transaction", "objectId": "facts"},
                "calculationType": "AggregateFirst",
                "formula": {
                    "displayText": "[quantity] * 2",
                    "languageVersion": "planning-v1",
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, grain)
    assert [r[cols["Double"]] for r in data["rows"]] == ["80.0", "120.0"]
    assert not client.get(BASE + "/dimensions").json()["items"]
    assert not [
        b
        for b in client.app.state.container.store.documents["engine_artifact_bindings"]
        if b.get("resourceKind") == "dimension"
    ]
