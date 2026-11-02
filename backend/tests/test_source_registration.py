from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from .conftest import ROOT, seed
from .fake_warehouse import FakeWarehouse
from .memory_store import MemoryStore
from .test_csv_sources import upload


@pytest.fixture
def registration_client(settings):
    store = MemoryStore()
    warehouse = FakeWarehouse()
    with TestClient(create_app(settings, store, warehouse)) as client:
        seed(client)
        assert (
            upload(
                client,
                b'product_id,Product Name,quantity\n001,"One, first",10.25\n',
                table="Uploaded",
            ).status_code
            == 201
        )
        yield client, warehouse, store


def register(client, **changes):
    return client.post(
        ROOT + "/source-registrations",
        json={
            "objectId": "source_uploaded",
            "tableName": "Uploaded",
            "keyColumnNames": ["product_id"],
            **changes,
        },
    )


def test_register_captures_schema_and_persists_across_app_instances(
    registration_client, settings
):
    client, warehouse, store = registration_client
    result = register(client)
    assert result.status_code == 201, result.text
    source = result.json()
    assert source["definition"]["keyColumns"] == ["col_1"]
    assert source["definition"]["columns"][1]["name"] == "Product Name"
    assert (
        source["definition"]["columns"][2]["physicalType"].replace(" ", "")
        == "Decimal(38,2)"
    )
    assert source["definition"]["inScope"] is True
    assert "displayName" not in source
    assert result.headers["etag"] == '"1"'
    with TestClient(create_app(settings, store, warehouse)) as second:
        assert second.get(ROOT + "/sources/source_uploaded").json() == source


@pytest.mark.parametrize("keys", [["unknown"], ["product_id", "product_id"]])
def test_invalid_keys_do_not_write_metadata(registration_client, keys):
    client, _, _ = registration_client
    assert register(client, keyColumnNames=keys).status_code in (400, 422)
    assert client.get(ROOT + "/sources/source_uploaded").status_code == 404


def test_rejects_nullable_key_and_untrusted_schema(registration_client):
    client, _, _ = registration_client
    assert (
        upload(
            client, b"id,quantity\n,1\nA,2\n", table="NullableKeys", key="csv_nullable"
        ).status_code
        == 201
    )
    assert (
        register(client, tableName="NullableKeys", keyColumnNames=["id"]).status_code
        == 409
    )
    assert register(client, columns=[{"name": "made_up"}]).status_code == 422


def test_scope_version_and_immutability(registration_client):
    client, _, _ = registration_client
    source = register(client, inScope=False).json()
    assert source["definition"]["inScope"] is False
    payload = {
        k: v
        for k, v in source.items()
        if k not in {"modelId", "revisionId", "version", "createdAt", "updatedAt"}
    }
    payload["definition"]["inScope"] = True
    assert (
        client.put(
            ROOT + "/sources/source_uploaded", json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        client.put(
            ROOT + "/sources/source_uploaded", json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 409
    )
    payload["definition"]["columns"][0]["name"] = "renamed"
    assert (
        client.put(
            ROOT + "/sources/source_uploaded", json=payload, headers={"If-Match": '"2"'}
        ).status_code
        == 409
    )
    revision = client.get(ROOT).json()
    assert (
        client.post(
            ROOT + "/publish",
            json={"expectedModelVersion": 2},
            headers={"If-Match": f'"{revision["version"]}"'},
        ).status_code
        == 200
    )
    assert register(client, objectId="after_publication").status_code == 409


def test_missing_table_and_offline_warehouse(registration_client):
    client, warehouse, _ = registration_client
    assert register(client, tableName="Absent").status_code == 404
    warehouse.available = False
    assert register(client).status_code == 503


def test_capture_only_unwraps_column_nullability(registration_client):
    client, warehouse, _ = registration_client
    parsed = warehouse.tables["Uploaded"]["parsed"]
    parsed.columns[1].type = "Array(Nullable(String))"
    parsed.columns[2].type = "LowCardinality(Nullable(String))"
    response = register(client)
    assert response.status_code == 201, response.text
    columns = response.json()["definition"]["columns"]
    assert columns[1]["physicalType"] == "Array(Nullable(String))"
    assert columns[1]["nullable"] is False
    assert columns[2]["physicalType"] == "LowCardinality(String)"
    assert columns[2]["nullable"] is True


def test_checkbox_registration_generates_internal_identity_without_keys(
    registration_client,
):
    client, _, _ = registration_client
    response = client.post(
        ROOT + "/source-registrations", json={"tableName": "Uploaded"}
    )
    assert response.status_code == 201, response.text
    source = response.json()
    assert source["objectId"].startswith("source_")
    assert source["definition"]["keyColumns"] == []
    assert source["definition"]["inScope"] is True
    # A repeat with the generated identity cannot create another semantic copy.
    assert (
        client.post(
            ROOT + "/source-registrations", json={"tableName": "Uploaded"}
        ).status_code
        == 409
    )
    level = {
        "objectId": "pl_uploaded",
        "name": "Uploaded",
        "displayName": "Uploaded",
        "definition": {
            "population": {"operation": "source", "sourceId": source["objectId"]},
            "grain": {
                "keyIds": ["product"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "product_id",
                        "populationRef": {"columnId": "col_1"},
                    }
                ],
            },
            "dimensionBindings": [],
        },
    }
    assert client.post(ROOT + "/planning-levels", json=level).status_code == 201
    transaction = {
        "objectId": "tx_uploaded",
        "name": "Uploaded",
        "displayName": "Uploaded",
        "definition": {
            "sourceId": source["objectId"],
            "planningLevelId": "pl_uploaded",
            "keyMappings": [{"sourceColumnId": "col_1", "grainColumnId": "product"}],
            "expectedRowsPerGrain": "zero_or_one",
        },
    }
    assert client.post(ROOT + "/transactions", json=transaction).status_code == 201
