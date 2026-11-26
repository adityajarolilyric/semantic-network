# ruff: noqa: F811
import time

import pytest

from backend.app.core.errors import MetadataError
from .test_csv_clickhouse import authored_clickhouse_app, clickhouse_app  # noqa: F401
from .test_network_build_improvements_clickhouse import seed
from .test_incremental_clickhouse import calc, run_incremental
from .test_measures_clickhouse import transaction, measure, values
from .test_planning_clickhouse import BASE, create, build, table

pytestmark = pytest.mark.integration


def active(client, kind, dimension):
    documents = client.app.state.container.store.documents
    binding = next(
        b
        for b in documents["engine_artifact_bindings"]
        if b.get("resourceKind") == kind and b.get("resourceId") == dimension
    )
    return next(
        a
        for a in documents["engine_artifacts"]
        if a["artifactId"] == binding["artifactId"]
    )


def test_current_storage_has_no_generations_cleans_clones_and_exposes_executed_sql(
    authored_clickhouse_app,
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    artifact = active(client, "measureValues", "base")
    columns = admin.query(
        f"DESCRIBE TABLE `{database}`.`{artifact['physicalName']}`"
    ).result_rows
    assert "__semantic_generation" not in [c[0] for c in columns]
    assert (
        admin.command(f"SELECT count() FROM `{database}`.`{artifact['viewName']}`") == 2
    )
    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows
    output = client.get(BASE + "/measures/execution/levels/base/output-sql")
    assert output.status_code == 200, output.text
    assert len(admin.query(output.json()["sql"]).result_rows) == 2
    for _ in range(20):
        trace = client.get(BASE + "/measures/execution/levels/base/build-sql")
        if trace.status_code == 200:
            break
        time.sleep(0.05)
    assert trace.status_code == 200, trace.text
    assert "INSERT INTO" in trace.json()["sql"]
    assert "ALTER TABLE" in trace.json()["sql"]
    assert "EXCHANGE TABLES" not in trace.json()["sql"]  # first publication is a rename
    calc(client, "Double")
    assert run_incremental(client)["state"] == "succeeded"
    current = active(client, "measureValues", "base")
    assert current["physicalName"] == artifact["physicalName"]
    assert current["sortKeyIds"] == artifact["sortKeyIds"]
    assert (
        len(
            admin.query(
                f"SELECT * FROM `{database}`.`{current['viewName']}`"
            ).result_rows
        )
        == 2
    )
    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows


def test_derived_dimension_and_values_share_one_table(authored_clickhouse_app):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    payload = {
        "objectId": "derived",
        "name": "Derived",
        "displayName": "Derived",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourceDimensionId": "base",
            },
            "grain": {
                "keyIds": ["product"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "product",
                        "populationRef": {"sourceGrainColumnId": "product"},
                    }
                ],
            },
            "populationAttributes": [],
            "memberBindings": [],
        },
    }
    create(client, payload)
    assert build(client, "derived")["state"] == "succeeded"
    population = active(client, "dimension", "derived")
    source = client.get(BASE + "/transactions/facts").json()["definition"]["sourceId"]
    transaction(client, "derived_facts", source, "derived", [("product", "col_1")])
    measure(client, "DerivedQty", "derived_facts", "derived", "col_2")
    assert run_incremental(client)["state"] == "succeeded"
    materialised = active(client, "measureValues", "derived")
    assert materialised["physicalName"] == population["physicalName"]
    assert table(client, "derived")["rows"] == [["P1"], ["P2"]]
    data, names = values(client, "derived")
    assert [r[names["DerivedQty"]] for r in data["rows"]] == ["40.0", "60.0"]
    assert (
        admin.command(
            "SELECT count() FROM system.tables WHERE database={db:String} AND name={name:String}",
            parameters={"db": database, "name": materialised["physicalName"]},
        )
        == 1
    )


def test_failed_clone_does_not_change_live_data_or_leave_a_table(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    original = active(client, "measureValues", "base")
    before = admin.query(
        f"SELECT * FROM `{database}`.`{original['physicalName']}` ORDER BY ALL"
    ).result_rows
    calc(client, "Double")
    writer = client.app.state.container.measure_builds.executor.in_place
    write = writer.build

    async def fail(*args, **kwargs):
        await write(*args, **kwargs)
        raise MetadataError("injected_failure", "Fail before exchange")

    monkeypatch.setattr(writer, "build", fail)
    assert run_incremental(client)["state"] == "failed"
    assert (
        admin.query(
            f"SELECT * FROM `{database}`.`{original['physicalName']}` ORDER BY ALL"
        ).result_rows
        == before
    )
    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows


def test_time_first_sort_order_uses_initial_population_and_is_persisted(
    authored_clickhouse_app,
):
    client, admin, database, _ = authored_clickhouse_app
    from .test_planning_clickhouse import managed

    source = managed(
        client,
        "CompositeFacts",
        b"product,location,week,quantity\nP1,L1,W1,10\nP2,L1,W1,20\nP3,L1,W2,30\n",
    )
    response = client.post(
        BASE + "/transactions",
        json={
            "objectId": "facts",
            "name": "Facts",
            "displayName": "Facts",
            "definition": {
                "sourceId": source,
                "grainColumnIds": ["col_1", "col_2", "col_3"],
                "timeColumnId": "col_3",
            },
            "measures": [
                {
                    "valueColumnId": "col_4",
                    "editable": False,
                    "aggregation": {"nonTime": "sum", "time": "sum"},
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert run_incremental(client, "full")["state"] == "succeeded"
    grain = client.get(BASE + "/transactions/facts").json()["effectiveGrainId"]
    artifact = active(client, "measureValues", grain)
    names = {c["columnId"]: c["name"] for c in artifact["columns"]}
    assert [names[key] for key in artifact["sortKeyIds"]] == [
        "week",
        "location",
        "product",
    ]
    sorting = admin.query(
        "SELECT sorting_key FROM system.tables WHERE database={db:String} AND name={name:String}",
        parameters={"db": database, "name": artifact["physicalName"]},
    ).first_row[0]
    assert sorting.index("week") < sorting.index("location") < sorting.index("product")
    assert run_incremental(client, "full")["state"] == "succeeded"
    assert (
        active(client, "measureValues", grain)["sortKeyIds"] == artifact["sortKeyIds"]
    )


def test_one_time_migration_preserves_active_edited_values(authored_clickhouse_app):
    from functools import partial
    from backend.scripts.migrate_current_storage import migrate

    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    artifact = active(client, "measureValues", "base")
    container = client.app.state.container
    old = "legacy_values"
    admin.command(
        f"CREATE TABLE `{database}`.`{old}` AS `{database}`.`{artifact['physicalName']}` ENGINE=MergeTree ORDER BY tuple()"
    )
    admin.command(
        f"ALTER TABLE `{database}`.`{old}` MODIFY COMMENT 'semanticLegacyTest'"
    )
    admin.command(
        f"INSERT INTO `{database}`.`{old}` SELECT * FROM `{database}`.`{artifact['physicalName']}`"
    )
    admin.command(
        f"ALTER TABLE `{database}`.`{old}` ADD COLUMN __semantic_generation String DEFAULT 'active'"
    )
    quantity = artifact["measureColumns"]["FQTY"]
    admin.command(
        f"ALTER TABLE `{database}`.`{old}` UPDATE `{quantity}`=45 WHERE product='P1' SETTINGS mutations_sync=2"
    )
    admin.command(f"DROP VIEW `{database}`.`{artifact['viewName']}`")
    admin.command(f"DROP TABLE `{database}`.`{artifact['physicalName']}` SYNC")
    legacy = {
        **artifact,
        "storageMode": "versioned",
        "physicalName": old,
        "storageOwner": "semanticLegacyTest",
        "generationToken": "active",
    }
    legacy.pop("viewName", None)

    async def replace():
        await container.store.repository.replace(
            "engine_artifacts", {"artifactId": artifact["artifactId"]}, legacy
        )

    client.portal.call(replace)
    client.portal.call(
        partial(migrate, container.store, container.warehouse_repository, apply=True)
    )
    current = active(client, "measureValues", "base")
    assert current["storageMode"] == "current"
    client.portal.call(
        partial(migrate, container.store, container.warehouse_repository, apply=True)
    )
    unchanged = run_incremental(client)
    assert unchanged["state"] == "succeeded" and unchanged.get("reused")
    result, cols = values(client, "base")
    assert [row[cols["FQTY"]] for row in result["rows"]] == ["45.0", "60.0"]
    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND name={name:String}",
        parameters={"db": database, "name": old},
    ).result_rows


def test_exchange_failure_restores_live_table_and_schema(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    original = active(client, "measureValues", "base")
    before = admin.query(
        f"SELECT * FROM `{database}`.`{original['physicalName']}` ORDER BY ALL"
    ).result_rows
    calc(client, "Double")
    storage = client.app.state.container.measure_builds.storage
    publish = storage._publish_one

    async def fail(artifact):
        await publish(artifact)
        raise MetadataError("injected_activation_failure", "Fail after exchange")

    monkeypatch.setattr(storage, "_publish_one", fail)
    assert run_incremental(client)["state"] == "failed"
    assert (
        admin.query(
            f"SELECT * FROM `{database}`.`{original['physicalName']}` ORDER BY ALL"
        ).result_rows
        == before
    )
    assert (
        admin.query(
            f"SELECT * FROM `{database}`.`{original['viewName']}` ORDER BY ALL"
        ).result_rows
        == before
    )
    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows


def test_pivot_output_sql_is_the_query_for_the_returned_cells(authored_clickhouse_app):
    client, admin, _, _ = authored_clickhouse_app
    seed(client)
    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"dimensionId": "base", "columnId": "product"}],
            "columns": [],
            "measures": ["FQTY"],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    rows = admin.query(
        data["sql"],
        settings=client.app.state.container.measure_builds.executor.execution_settings(),
    ).result_rows
    cell = client.app.state.container.warehouse_repository.cell
    assert [[cell(v) for v in row[:2]] for row in rows] == data["rows"]


def test_measure_rename_preserves_values_in_the_clone(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    artifact = active(client, "measureValues", "base")
    physical = artifact["measureColumns"]["FQTY"]
    admin.command(
        f"ALTER TABLE `{database}`.`{artifact['physicalName']}` UPDATE `{physical}`=45 WHERE product='P1' SETTINGS mutations_sync=2"
    )
    original = client.get(BASE + "/measures/FQTY").json()
    payload = {
        k: v
        for k, v in original.items()
        if k not in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
    }
    payload.update(name="Forecast Quantity", displayName="Forecast Quantity")
    updated = client.put(
        BASE + "/measures/FQTY",
        json=payload,
        headers={"If-Match": str(original["version"])},
    )
    assert updated.status_code == 200, updated.text
    result = run_incremental(client)
    assert result["state"] == "succeeded", result
    data, names = values(client, "base")
    assert [row[names["Forecast Quantity"]] for row in data["rows"]] == ["45.0", "60.0"]
    current = active(client, "measureValues", "base")
    assert current["measureColumns"]["FQTY"] == physical
    calc(client, "Double")
    storage = client.app.state.container.measure_builds.storage
    publish = storage._publish_one

    async def fail(artifact):
        await publish(artifact)
        raise MetadataError(
            "injected_activation_failure", "Fail after aliased view exchange"
        )

    monkeypatch.setattr(storage, "_publish_one", fail)
    assert run_incremental(client)["state"] == "failed"
    rows = admin.query(
        f"SELECT `Forecast Quantity` FROM `{database}`.`{current['viewName']}` ORDER BY product"
    ).result_rows
    assert rows == [(45.0,), (60.0,)]
    monkeypatch.setattr(storage, "_publish_one", publish)
    assert run_incremental(client)["state"] == "succeeded"
    data, names = values(client, "base")
    assert [row[names["Double"]] for row in data["rows"]] == ["90.0", "120.0"]


def test_completed_build_cleanup_is_retried_from_durable_pending_queue(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    calc(client, "Double")
    container = client.app.state.container
    storage = container.measure_builds.storage
    finish = storage.finish

    async def deferred(_artifacts):
        raise RuntimeError("Simulate a crash before cleanup")

    monkeypatch.setattr(storage, "finish", deferred)
    assert run_incremental(client)["state"] == "succeeded"
    assert admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows
    monkeypatch.setattr(storage, "finish", finish)
    client.portal.call(container.engine_worker.collector.recover_pending)
    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows
    current = active(client, "measureValues", "base")
    assert not current["cleanupPending"]
    result, names = values(client, "base")
    assert [row[names["Double"]] for row in result["rows"]] == ["80.0", "120.0"]


def test_full_refresh_recreates_legacy_view_name_without_renaming_raw_source(
    authored_clickhouse_app,
):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    container = client.app.state.container
    documents = container.store.documents
    source_id = client.get(BASE + "/transactions/facts").json()["definition"][
        "sourceId"
    ]
    binding = next(
        b
        for b in documents["engine_artifact_bindings"]
        if b.get("sourceId") == source_id
    )
    artifact = next(
        a
        for a in documents["engine_artifacts"]
        if a["artifactId"] == binding["artifactId"]
    )
    assert artifact["physicalName"].startswith("trans_Facts_")
    legacy = "__semantic_view_legacy_test"
    admin.command(
        f"RENAME TABLE `{database}`.`{artifact['physicalName']}` TO `{database}`.`{legacy}`"
    )

    async def legacy_metadata():
        await container.store.repository.replace(
            "engine_artifacts",
            {"artifactId": artifact["artifactId"]},
            {**artifact, "physicalName": legacy, "viewNamingPolicy": None},
        )

    client.portal.call(legacy_metadata)
    incremental = run_incremental(client)
    assert incremental["state"] == "succeeded", incremental
    still = next(
        a
        for a in documents["engine_artifacts"]
        if a["artifactId"] == binding["artifactId"]
    )
    assert still["physicalName"] == legacy
    refreshed = run_incremental(client, "full")
    assert refreshed["state"] == "succeeded", refreshed
    fresh_binding = next(
        b
        for b in documents["engine_artifact_bindings"]
        if b.get("sourceId") == source_id
    )
    fresh = next(
        a
        for a in documents["engine_artifacts"]
        if a["artifactId"] == fresh_binding["artifactId"]
    )
    assert fresh["physicalName"].startswith("trans_Facts_")
    assert fresh["artifactId"] != artifact["artifactId"]
    assert admin.command(f"SELECT count() FROM `{database}`.`Facts`") == 2
    result, names = values(client, "base")
    assert [r[names["FQTY"]] for r in result["rows"]] == ["40.0", "60.0"]
    assert run_incremental(client, "full")["state"] == "succeeded"
    assert (
        next(
            b
            for b in documents["engine_artifact_bindings"]
            if b.get("sourceId") == source_id
        )["artifactId"]
        == fresh["artifactId"]
    )
