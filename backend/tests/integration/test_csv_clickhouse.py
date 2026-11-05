from concurrent.futures import ThreadPoolExecutor
import csv
import io
import os
from uuid import uuid4

import clickhouse_connect
from fastapi.testclient import TestClient
import pytest

from backend.app.core.config import Settings
from backend.tests.fixture_app import create_fixture_app as create_app
from backend.app.warehouse.csv_parser import ParsedCsv
from backend.app.warehouse.schemas import WarehouseColumn
from backend.tests.memory_store import MemoryStore
from backend.tests.test_csv_sources import ROOT, upload

pytestmark = pytest.mark.integration


@pytest.fixture
def clickhouse_app():
    port = os.getenv("METADATA_TEST_CH_PORT")
    if not port:
        pytest.skip("Set METADATA_TEST_CH_PORT to an isolated ClickHouse test server")
    host = os.getenv("METADATA_TEST_CH_HOST", "127.0.0.1")
    password = os.getenv("METADATA_TEST_CH_PASSWORD", "")
    admin = clickhouse_connect.get_client(
        host=host, port=int(port), username="default", password=password
    )
    database = "csv_v1_test_" + uuid4().hex
    admin.command(f"CREATE DATABASE `{database}`")
    settings = Settings(
        _env_file=None,
        environment="test",
        clickhouse_host=host,
        clickhouse_port=int(port),
        clickhouse_password=password,
        clickhouse_database=database,
    )
    try:
        with TestClient(create_app(settings, MemoryStore())) as client:
            yield client, admin, database, settings
    finally:
        assert database.startswith("csv_v1_test_")
        admin.command(f"DROP DATABASE IF EXISTS `{database}` SYNC")
        admin.close()


def test_real_csv_import_catalog_and_preview(clickhouse_app):
    client, _, _, _ = clickhouse_app
    response = upload(
        client,
        b"product_id,quantity,price,date,active,notes\n001,10,123.456,2026-10-31,true,\n002,20,0.001,2026-11-01,false,hello\n",
    )
    assert response.status_code == 201, response.text
    assert response.json()["rowCount"] == 2
    preview = client.get(ROOT + "/tables/Forecast/preview?limit=1&offset=1")
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"] == [
        ["002", "20", "0.001", "2026-11-01", "false", "hello"]
    ]
    catalog = client.get(ROOT + "/tables")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["tables"] == [
        {"name": "Forecast", "engine": "MergeTree", "rowCount": 2, "columnCount": 6}
    ]


def test_existing_table_and_idempotent_retry(clickhouse_app):
    client, _, _, _ = clickhouse_app
    assert upload(client, b"x\n10\n").status_code == 201
    assert upload(client, b"x\n10\n").json()["replayed"] is True
    assert upload(client, b"x\n99\n", key="csv_other").status_code == 409
    assert client.get(ROOT + "/tables/Forecast/preview").json()["rows"] == [["10"]]


def test_original_headers_quotes_multiline_and_injection_safety(clickhouse_app):
    client, _, _, _ = clickhouse_app
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    headers = ["Product ID", "a`b", "quoted,column"]
    writer.writerow(headers)
    writer.writerow(["001", "line one\nline two", "Café, small"])
    result = upload(client, buffer.getvalue().encode())
    assert result.status_code == 201, result.text
    preview = client.get(ROOT + "/tables/Forecast/preview")
    assert preview.status_code == 200, preview.text
    assert [column["name"] for column in preview.json()["columns"]] == headers
    assert preview.json()["rows"] == [["001", "line one\nline two", "Café, small"]]
    assert upload(client, b"x\n1\n", table="bad;DROP_DATABASE").status_code == 422


def test_5000_row_limit_is_enforced_before_creation(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    assert upload(client, b"x\n" + b"1\n" * 5000).status_code == 201
    assert (
        upload(
            client, b"x\n" + b"1\n" * 5001, table="TooLarge", key="csv_big"
        ).status_code
        == 422
    )
    assert int(admin.command(f"SELECT count() FROM `{database}`.`Forecast`")) == 5000
    assert admin.command(f"EXISTS TABLE `{database}`.`TooLarge`") == 0


def test_insert_failure_does_not_publish_or_leave_staging(clickhouse_app, monkeypatch):
    client, admin, database, _ = clickhouse_app
    parser = client.app.state.container.warehouse_sources.parser
    monkeypatch.setattr(
        parser,
        "parse",
        lambda _: ParsedCsv(
            columns=[WarehouseColumn(name="x", type="Nullable(Int64)")],
            rows=[["not-an-int"]],
        ),
    )
    response = upload(client, b"x\n1\n")
    assert response.status_code == 503
    assert admin.command(f"EXISTS TABLE `{database}`.`Forecast`") == 0
    rows = admin.query(
        "SELECT name FROM system.tables WHERE database={db:String}",
        parameters={"db": database},
    ).result_rows
    assert rows == []


def test_concurrent_clients_never_replace_each_other(clickhouse_app):
    first, _, _, settings = clickhouse_app
    with TestClient(create_app(settings, MemoryStore())) as second:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(upload, first, b"x\n1\n", "Race", "csv_a"),
                pool.submit(upload, second, b"x\n2\n", "Race", "csv_b"),
            ]
            results = [future.result() for future in futures]
        assert sorted(response.status_code for response in results) == [201, 409], [
            response.text for response in results
        ]
        assert first.get(ROOT + "/tables/Race/preview").json()["rows"] in (
            [["1"]],
            [["2"]],
        )
