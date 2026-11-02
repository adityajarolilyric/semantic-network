from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from backend.app.core.errors import MetadataError
from backend.app.main import create_app
from backend.app.warehouse.csv_parser import CsvParser
from backend.app.warehouse.repository import quote_identifier, validate_table_name
from .fake_warehouse import FakeWarehouse
from .memory_store import MemoryStore

ROOT = "/api/v1/warehouse"


@pytest.fixture
def warehouse_client(settings):
    warehouse = FakeWarehouse()
    with TestClient(create_app(settings, MemoryStore(), warehouse)) as client:
        yield client, warehouse


def upload(
    client, content, table="Forecast", key="csv_test_1", filename="forecast.csv"
):
    return client.post(
        ROOT + "/imports/csv",
        data={"tableName": table},
        files={"file": (filename, content, "text/csv")},
        headers={"Idempotency-Key": key},
    )


def test_original_headers_quoting_multiline_and_utf8():
    parsed = CsvParser().parse(
        '\ufeffproduct_id,Product Name,notes\r\n001,"Café, small","line one\nline two"\r\n'.encode()
    )
    assert [col.name for col in parsed.columns] == [
        "product_id",
        "Product Name",
        "notes",
    ]
    assert parsed.rows == [["001", "Café, small", "line one\nline two"]]
    assert parsed.columns[0].type == "String"


def test_infers_lossless_numeric_date_boolean_and_null():
    parsed = CsvParser().parse(
        b"quantity,price,date,flag,empty\n10,123.456,2026-10-31,true,\n,0.001,2026-11-01,false,\n"
    )
    assert [col.type for col in parsed.columns] == [
        "Nullable(Int64)",
        "Decimal(38,3)",
        "Date32",
        "Bool",
        "Nullable(String)",
    ]
    assert parsed.rows[0] == [10, Decimal("123.456"), date(2026, 10, 31), True, None]
    assert parsed.rows[1][0] is None


@pytest.mark.parametrize(
    "values,expected",
    [
        (["001", "002"], "String"),
        (["1", "two"], "String"),
        (["1e4", "2e4"], "String"),
        (["9223372036854775808"], "Decimal(38,0)"),
        (["9" * 39], "String"),
        (["2026-02-30"], "String"),
        (["2300-01-01"], "String"),
        ([" 10 "], "String"),
    ],
)
def test_conservative_inference(values, expected):
    assert CsvParser.infer(values) == expected


@pytest.mark.parametrize(
    "content,code",
    [
        (b"", "missing_header"),
        (b"a,a\n1,2\n", "duplicate_header"),
        (b"a,\n1,2\n", "invalid_header"),
        (b" a,b\n1,2\n", "invalid_header"),
        (b"a,b\n1\n", "row_width"),
        (b"a,b\n1,2,3\n", "row_width"),
        (b'a,b\n"unterminated,2\n', "invalid_csv"),
        (b"a\n\xff\n", "invalid_encoding"),
        (b"a\nx\x00y\n", "invalid_csv"),
    ],
)
def test_bad_csv_is_rejected(content, code):
    with pytest.raises(MetadataError) as error:
        CsvParser().parse(content)
    assert error.value.code == code


def test_row_limit_is_data_records_not_physical_lines():
    parser = CsvParser()
    assert len(parser.parse(b"x\n" + b'"a\nb"\n' * 5000).rows) == 5000
    with pytest.raises(MetadataError) as error:
        parser.parse(b"x\n" + b"1\n" * 5001)
    assert error.value.code == "row_limit"


def test_header_only_and_cell_column_limits():
    assert CsvParser().parse(b"a,b\n").rows == []
    with pytest.raises(MetadataError):
        CsvParser(max_columns=1).parse(b"a,b\n")
    with pytest.raises(MetadataError):
        CsvParser(max_cell_chars=3).parse(b"x\n1234\n")


def test_upload_catalog_preview_and_pagination(warehouse_client):
    client, _ = warehouse_client
    response = upload(
        client, b"product_id,quantity,date\n001,10,2026-10-31\n002,20,2026-11-01\n"
    )
    assert response.status_code == 201, response.text
    assert response.json()["rowCount"] == 2
    assert client.get(ROOT + "/tables").json()["tables"][0]["name"] == "Forecast"
    preview = client.get(
        ROOT + "/tables/Forecast/preview", params={"limit": 1, "offset": 1}
    ).json()
    assert preview["total"] == 2 and preview["rows"] == [["002", "20", "2026-11-01"]]
    assert preview["offset"] == 1 and preview["limit"] == 1


def test_duplicate_preserves_table_and_same_request_replays(warehouse_client):
    client, _ = warehouse_client
    content = b"x\n10\n"
    assert upload(client, content).status_code == 201
    assert upload(client, content).json()["replayed"] is True
    assert upload(client, b"x\n20\n", key="csv_other").status_code == 409
    assert upload(client, b"x\n20\n").status_code == 409
    assert client.get(ROOT + "/tables/Forecast/preview").json()["rows"] == [["10"]]


def test_invalid_upload_never_creates_a_table(warehouse_client):
    client, warehouse = warehouse_client
    assert upload(client, b"x\n" + b"1\n" * 5001).status_code == 422
    assert not warehouse.tables
    assert upload(client, b"x\n1\n", filename="file.txt").status_code == 422
    assert upload(client, b"x\n1\n", table="bad; DROP TABLE x").status_code == 422
    assert not warehouse.tables


def test_upload_requires_one_file_and_no_database_field(warehouse_client):
    client, _ = warehouse_client
    response = client.post(
        ROOT + "/imports/csv",
        data={"tableName": "X", "database": "other"},
        files={"file": ("x.csv", b"x\n1\n")},
        headers={"Idempotency-Key": "csv_test"},
    )
    assert response.status_code == 422
    response = client.post(
        ROOT + "/imports/csv",
        data={"tableName": "X"},
        files=[("file", ("x.csv", b"x\n1\n")), ("file", ("y.csv", b"y\n2\n"))],
        headers={"Idempotency-Key": "csv_test"},
    )
    assert response.status_code == 422
    assert (
        client.post(
            ROOT + "/imports/csv",
            data={"tableName": "X"},
            files={"file": ("x.csv", b"x\n1\n")},
        ).status_code
        == 422
    )


def test_limits_status_and_bad_preview(warehouse_client):
    client, warehouse = warehouse_client
    assert client.get(ROOT + "/status").json()["maxCsvRows"] == 5000
    warehouse.available = False
    assert client.get(ROOT + "/status").json()["connected"] is False
    assert client.get(ROOT + "/tables").status_code == 503
    assert client.get(ROOT + "/tables/X/preview?limit=201").status_code == 422
    assert client.get(ROOT + "/tables/X/preview?offset=-1").status_code == 422


def test_file_and_multipart_body_limits(settings):
    settings.csv_max_file_bytes = 1024
    with TestClient(create_app(settings, MemoryStore(), FakeWarehouse())) as client:
        assert upload(client, b"x\n" + b"a" * 1024).status_code == 413
        assert upload(client, b"x\n" + b"a" * 70000).status_code == 413


def test_identifier_escaping_and_reserved_names():
    assert quote_identifier("a`b") == "`a\\`b`"
    with pytest.raises(MetadataError):
        validate_table_name("__csv_ingest_reserved")
    with pytest.raises(MetadataError):
        validate_table_name("grain_PLC_mat_" + "a" * 28)
    validate_table_name("grain_inventory")
