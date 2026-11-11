"""Saved object views isolate transformations and atomically publish with metadata."""

# ruff: noqa: F811
from copy import deepcopy
import os
from uuid import uuid4
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pydantic import SecretStr
from backend.tests.fixture_app import create_fixture_app

import pytest

from backend.app.core.errors import MetadataError
from .test_csv_clickhouse import authored_clickhouse_app, clickhouse_app  # noqa: F401
from .test_object_pivots_clickhouse import pivot, schema
from .test_planning_clickhouse import managed, BASE
from .test_measures_clickhouse import assert_post

pytestmark = pytest.mark.integration


def raw_dimension(source):
    return {
        "objectId": "Location",
        "kind": "dimensionMembers",
        "name": "Location",
        "displayName": "Location",
        "isTimeDim": False,
        "definition": {
            "sourceId": source,
            "columns": [
                {
                    "columnId": f"col_{i}",
                    "sourceColumnId": f"col_{i}",
                    "name": name,
                    "displayName": name,
                }
                for i, name in enumerate(["location_id", "attribute", "value"], 1)
            ],
            "keyColumnIds": ["col_1", "col_2"],
            "labelColumnId": "col_1",
            "attributeColumnIds": ["col_1", "col_2", "col_3"],
        },
    }


def seed(client):
    source = managed(
        client,
        "Locations",
        b"location_id,attribute,value\nL1,zone,North\nL1,city,Mumbai\nL2,zone,South\nL2,city,Delhi\n",
    )
    document = raw_dimension(source)
    saved = assert_post(client, "/dimension-members", document)
    return source, document, saved


def view_name(client, object_id):
    async def read():
        repository = client.app.state.container.store.repository
        binding = await repository.get(
            "engine_artifact_bindings",
            {"resourceKind": "objectView", "resourceId": object_id},
        )
        artifact = await repository.get(
            "engine_artifacts", {"artifactId": binding["artifactId"]}
        )
        return artifact["physicalName"]

    return client.portal.call(read)


def test_location_save_updates_its_view_and_preview_does_not_compile(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    source, definition, saved = seed(client)
    owner = client.portal.call(
        client.app.state.container.dimension_members.owner,
        "default_model",
        "revision_1",
        "Location",
    )
    original_name = view_name(client, owner["objectId"])
    p = pivot(["col_1"], ["zone", "city"], keys=["col_1"])
    columns = schema(client, "dimension-members", "Location", source, p)
    changed = deepcopy(definition)
    changed["definition"].update(
        pivot=p,
        columns=[
            {
                "columnId": c["columnId"],
                "sourceColumnId": c["columnId"],
                "name": c["name"],
                "displayName": "Location ID"
                if c["name"] == "location_id"
                else c["name"],
            }
            for c in columns
        ],
        keyColumnIds=["col_1"],
        attributeColumnIds=[c["columnId"] for c in columns],
    )
    response = client.put(
        BASE + "/dimension-members/Location",
        json=changed,
        headers={"If-Match": str(saved["version"])},
    )
    assert response.status_code == 200, response.text
    assert view_name(client, owner["objectId"]) == original_name

    async def cannot_compile(*args, **kwargs):
        raise AssertionError("Preview must read the saved view")

    monkeypatch.setattr(
        client.app.state.container.object_views.planner, "compile", cannot_compile
    )
    preview = client.get(BASE + "/dimension-members/Location/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["rowCount"] == 2
    assert preview.json()["columns"][0]["name"] == "Location ID"
    assert {tuple(row) for row in preview.json()["rows"]} == {
        ("L1", "North", "Mumbai"),
        ("L2", "South", "Delhi"),
    }
    raw = client.get("/api/v1/warehouse/tables/Locations/preview?limit=50")
    assert raw.status_code == 200, raw.text
    assert raw.json()["total"] == 4
    assert [c["name"] for c in raw.json()["columns"]] == [
        "location_id",
        "attribute",
        "value",
    ]
    views = admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND engine='View'",
        parameters={"db": database},
    ).result_rows
    assert views == [(original_name,)]


def test_one_source_can_have_independent_dimension_and_transaction_views(
    authored_clickhouse_app,
):
    client, admin, database, _ = authored_clickhouse_app
    source, _, _ = seed(client)
    p = pivot(["col_1"], ["zone"], keys=["col_1"])
    columns = schema(client, "transactions", "location_tx", source, p)
    transaction = {
        "kind": "transaction",
        "objectId": "location_tx",
        "name": "Location transactions",
        "displayName": "Location transactions",
        "definition": {"sourceId": source, "grainColumnIds": ["col_1"], "pivot": p},
    }
    assert_post(client, "/transactions", transaction)
    preview = client.get(BASE + "/transactions/location_tx/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["rowCount"] == 2
    assert [c["name"] for c in preview.json()["columns"]] == [
        c["name"] for c in columns
    ]
    assert {tuple(row) for row in preview.json()["rows"]} == {
        ("L1", "North"),
        ("L2", "South"),
    }
    dimension = client.get(BASE + "/dimension-members/Location/preview")
    assert dimension.status_code == 200, dimension.text
    assert dimension.json()["rowCount"] == 4
    assert (
        admin.command(
            "SELECT count() FROM system.tables WHERE database={db:String} AND engine='View'",
            parameters={"db": database},
        )
        == 2
    )
    assert admin.command(f"SELECT count() FROM `{database}`.Locations") == 4


def test_failed_view_publication_restores_view_and_metadata(
    authored_clickhouse_app, monkeypatch
):
    client, admin, database, _ = authored_clickhouse_app
    _, definition, saved = seed(client)
    owner = client.portal.call(
        client.app.state.container.dimension_members.owner,
        "default_model",
        "revision_1",
        "Location",
    )
    name = view_name(client, owner["objectId"])
    before_uuid = admin.command(
        "SELECT toString(uuid) FROM system.tables WHERE database={db:String} AND name={name:String}",
        parameters={"db": database, "name": name},
    )
    storage = client.app.state.container.object_views.storage
    publish = storage.publish

    async def fail_after_exchange(*args, **kwargs):
        await publish(*args, **kwargs)
        raise MetadataError("injected_failure", "Injected publication failure")

    monkeypatch.setattr(storage, "publish", fail_after_exchange)
    changed = {
        **definition,
        "name": "Changed Location",
        "displayName": "Changed Location",
    }
    response = client.put(
        BASE + "/dimension-members/Location",
        json=changed,
        headers={"If-Match": str(saved["version"])},
    )
    assert response.status_code >= 400, response.text
    assert client.get(BASE + "/dimension-members/Location").json()["name"] == "Location"
    assert (
        admin.command(
            "SELECT toString(uuid) FROM system.tables WHERE database={db:String} AND name={name:String}",
            parameters={"db": database, "name": name},
        )
        == before_uuid
    )
    assert (
        client.get(BASE + "/dimension-members/Location/preview").json()["rowCount"] == 4
    )
    assert (
        admin.command(
            "SELECT count() FROM system.tables WHERE database={db:String} AND engine='View'",
            parameters={"db": database},
        )
        == 1
    )


def test_saved_view_publication_uses_real_mongo_transactions(
    clickhouse_app, monkeypatch
):
    uri = os.getenv("METADATA_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set METADATA_TEST_MONGO_URI to a dedicated MongoDB replica set")
    _, admin, database, settings = clickhouse_app
    metadata_database = "metadata_v1_test_" + uuid4().hex
    configured = settings.model_copy(
        update={"mongo_uri": SecretStr(uri), "mongo_database": metadata_database}
    )
    try:
        with TestClient(create_fixture_app(configured)) as client:
            client.app.state.container.warehouse_sources.upload_metadata = None
            test_failed_view_publication_restores_view_and_metadata(
                (client, admin, database, configured), monkeypatch
            )
            with MongoClient(uri) as mongo:
                assert (
                    mongo[metadata_database].engine_runs.count_documents(
                        {"resourceKind": "objectView", "state": "succeeded"}
                    )
                    == 1
                )
                assert (
                    mongo[metadata_database].engine_runs.count_documents(
                        {"resourceKind": "objectView", "state": "failed"}
                    )
                    == 1
                )
    finally:
        with MongoClient(uri) as mongo:
            mongo.drop_database(metadata_database)


def test_save_repairs_missing_view_without_changing_its_name(authored_clickhouse_app):
    client, admin, database, _ = authored_clickhouse_app
    _, definition, saved = seed(client)
    owner = client.portal.call(
        client.app.state.container.dimension_members.owner,
        "default_model",
        "revision_1",
        "Location",
    )
    name = view_name(client, owner["objectId"])
    admin.command(f"DROP VIEW `{database}`.`{name}`")
    response = client.put(
        BASE + "/dimension-members/Location",
        json=definition,
        headers={"If-Match": str(saved["version"])},
    )
    assert response.status_code == 200, response.text
    assert view_name(client, owner["objectId"]) == name
    assert (
        client.get(BASE + "/dimension-members/Location/preview").json()["rowCount"] == 4
    )


def test_save_retires_an_existing_identity_wrapper(authored_clickhouse_app):
    client, admin, database, _ = authored_clickhouse_app
    source = managed(
        client,
        "Locations",
        b"location_id,attribute,value\nL1,zone,North\nL1,city,Mumbai\n",
    )
    store = client.app.state.container.store

    async def legacy_reference():
        binding = await store.repository.get(
            "engine_artifact_bindings", {"sourceId": source}
        )
        return await store.repository.get(
            "engine_artifacts", {"artifactId": binding["artifactId"]}
        )

    artifact = client.portal.call(legacy_reference)
    admin.command(
        f"CREATE VIEW `{database}`.legacy_identity AS SELECT * FROM `{database}`.Locations"
    )
    admin.command(
        f"ALTER TABLE `{database}`.legacy_identity MODIFY COMMENT 'semanticArtifact:{artifact['artifactId']}'"
    )

    async def restore_legacy(repo):
        await repo.replace(
            "engine_artifacts",
            {"artifactId": artifact["artifactId"]},
            {
                **artifact,
                "physicalName": "legacy_identity",
                "kind": "view",
                "storageMode": "legacy_identity",
            },
        )

    client.portal.call(store.transaction, restore_legacy)
    assert_post(client, "/dimension-members", raw_dimension(source))
    assert (
        admin.command(
            "SELECT count() FROM system.tables WHERE database={db:String} AND engine='View'",
            parameters={"db": database},
        )
        == 1
    )
    assert (
        client.get(BASE + "/dimension-members/Location/preview").json()["rowCount"] == 2
    )
    assert (
        client.get(BASE + "/managed-tables/" + source + "/preview").status_code == 200
    )
    assert admin.command(f"SELECT count() FROM `{database}`.Locations") == 2
