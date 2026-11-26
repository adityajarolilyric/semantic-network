# ruff: noqa: F811
from types import SimpleNamespace
import pytest

from backend.app.core.errors import MetadataError
from backend.app.warehouse.measure_columns import MeasureColumnSql
from .test_csv_clickhouse import authored_clickhouse_app, clickhouse_app  # noqa: F401
from .test_current_storage_clickhouse import active
from .test_network_build_improvements_clickhouse import seed
from .test_incremental_clickhouse import calc, run_incremental
from .test_measures_clickhouse import measure, transaction, values, update_measure
from .test_planning_clickhouse import BASE
from .test_measure_edits_clickhouse import edit
from .test_managed_clickhouse import await_run

pytestmark = pytest.mark.integration


def test_added_reference_and_formula_preserve_edited_measure(authored_clickhouse_app):
    client, admin, database, _ = authored_clickhouse_app
    seed(client)
    update_measure(
        client, "FQTY", lambda d: d.update(editable=True, allocation={"mode": "equal"})
    )
    assert run_incremental(client)["state"] == "succeeded"
    # An actual Decision View edit, not an out-of-band source mutation.
    view = {"dimensionId": "base", "columnId": "product"}
    edited = await_run(
        client, BASE, edit(client, value=120, rows=[view], members=["P1"])
    )
    assert edited["state"] == "succeeded", edited
    prior = active(client, "measureValues", "base")
    source = client.get(BASE + "/transactions/facts").json()["definition"]["sourceId"]
    transaction(client, "sales", source, "base", [("product", "col_1")])
    measure(client, "SQTY", "sales", "base", "col_2")
    calc(client, "Double", dep="FQTY")
    assert run_incremental(client)["state"] == "succeeded"
    result, cols = values(client, "base")
    assert [r[cols["FQTY"]] for r in result["rows"]] == ["120.0", "60.0"]
    assert [r[cols["SQTY"]] for r in result["rows"]] == ["40.0", "60.0"]
    assert [r[cols["Double"]] for r in result["rows"]] == ["240.0", "120.0"]
    assert (
        active(client, "measureValues", "base")["physicalName"] == prior["physicalName"]
    )
    assert not admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND position(name,'_build_')>0",
        parameters={"db": database},
    ).result_rows


@pytest.mark.parametrize(
    "problem", ["duplicate", "missing", "wrong_key", "null_value", "null_key"]
)
def test_invalid_column_stage_never_changes_candidate(authored_clickhouse_app, problem):
    client, admin, database, settings = authored_clickhouse_app
    warehouse = client.app.state.container.warehouse_repository
    # Use the shared warehouse facade and private test database only.
    writer = MeasureColumnSql(warehouse)
    artifact = {"physicalName": "candidate", "buildOwner": "test-owner"}
    table = f"`{database}`.`candidate`"
    admin.command(
        f"CREATE TABLE {table} (pid Nullable(String), qty Nullable(Float64)) ENGINE=MergeTree ORDER BY tuple() COMMENT 'test-owner'"
    )
    admin.command(f"INSERT INTO {table} VALUES ('P1', 120), ('P2', 60)")
    relation = {
        "duplicate": "SELECT 'P1' AS pid, toFloat64(1) AS qty FROM numbers(2)",
        "missing": "SELECT 'P1' AS pid, toFloat64(1) AS qty",
        "wrong_key": "SELECT if(number=0,'P1','P3') AS pid, toFloat64(1) AS qty FROM numbers(2)",
        "null_value": "SELECT if(number=0,'P1','P2') AS pid, CAST(NULL AS Nullable(Float64)) AS qty FROM numbers(2)",
        "null_key": "SELECT if(number=0,'P1',NULL) AS pid, toFloat64(1) AS qty FROM numbers(2)",
    }[problem]
    keys = [{"name": "pid", "storageName": "pid"}]
    columns = [
        {
            "name": "qty",
            "storageName": "qty",
            "nullable": False,
            "physicalType": "Float64",
        }
    ]
    with pytest.raises(MetadataError):
        writer.write(
            admin,
            artifact,
            relation,
            keys,
            columns,
            {"max_memory_usage": 512 * 1024**2, "join_use_nulls": 1},
            2,
        )
    assert admin.query(f"SELECT qty FROM {table} ORDER BY pid").result_rows == [
        (120.0,),
        (60.0,),
    ]
    assert admin.query(
        "SELECT name FROM system.tables WHERE database={db:String}",
        parameters={"db": database},
    ).result_rows == [("candidate",)]


def test_column_writer_only_receives_scalars_and_preserves_nulls(
    authored_clickhouse_app,
):
    client, admin, database, _ = authored_clickhouse_app
    writer = MeasureColumnSql(client.app.state.container.warehouse_repository)
    artifact = {"physicalName": "candidate", "buildOwner": "test-owner"}
    table = f"`{database}`.`candidate`"
    admin.command(
        f"CREATE TABLE {table} (pid String, qty Nullable(Float64), label Nullable(String), untouched Float64) ENGINE=MergeTree ORDER BY pid COMMENT 'test-owner'"
    )
    admin.command(f"INSERT INTO {table} VALUES ('P1',0,'old',120),('P2',0,'old',60)")

    class ScalarOnlyClient:
        def command(self, *args, **kwargs):
            return admin.command(*args, **kwargs)

        def query(self, sql, *args, **kwargs):
            result = admin.query(sql, *args, **kwargs)
            if "system.tables" in sql:
                return result  # bounded ownership metadata, never fact data
            return SimpleNamespace(first_row=result.first_row)

    columns = [
        {
            "name": "qty",
            "storageName": "qty",
            "nullable": True,
            "physicalType": "Float64",
        },
        {
            "name": "label",
            "storageName": "label",
            "nullable": True,
            "physicalType": "String",
        },
    ]
    relation = "SELECT if(number=0,'P1','P2') AS pid, if(number=0,42.5,NULL) AS qty, if(number=0,NULL,'Ready') AS label FROM numbers(2)"
    writer.write(
        ScalarOnlyClient(),
        artifact,
        relation,
        [{"name": "pid", "storageName": "pid"}],
        columns,
        {"max_memory_usage": 512 * 1024**2, "join_use_nulls": 1},
        2,
    )
    assert admin.query(
        f"SELECT qty,label,untouched FROM {table} ORDER BY pid"
    ).result_rows == [(42.5, None, 120.0), (None, "Ready", 60.0)]


def test_nested_formulas_checkpoint_changed_inputs(authored_clickhouse_app):
    client, _, _, _ = authored_clickhouse_app
    seed(client)
    calc(client, "Double")
    calc(client, "Triple", dep="Double", factor=3)
    calc(client, "Top", dep="Triple", factor=4)
    assert run_incremental(client)["state"] == "succeeded"
    result, cols = values(client, "base")
    assert [
        [r[cols[mid]] for mid in ("Double", "Triple", "Top")] for r in result["rows"]
    ] == [["80.0", "240.0", "960.0"], ["120.0", "360.0", "1440.0"]]
    assert active(client, "measureValues", "base")["writeGroups"] == [
        ["Double"],
        ["Triple"],
        ["Top"],
    ]
    update_measure(
        client, "Double", lambda d: d["formula"]["expression"]["right"].update(value=3)
    )
    assert run_incremental(client)["state"] == "succeeded"
    result, cols = values(client, "base")
    assert [
        [r[cols[mid]] for mid in ("FQTY", "Double", "Triple", "Top")]
        for r in result["rows"]
    ] == [["40.0", "120.0", "360.0", "1440.0"], ["60.0", "180.0", "540.0", "2160.0"]]
