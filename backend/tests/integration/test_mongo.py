from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure
import pytest

from backend.app.core.config import Settings
from backend.tests.fixture_app import create_fixture_app as create_app
from backend.tests.conftest import ROOT, seed
from backend.tests.test_metadata_api import definition, publish

pytestmark = pytest.mark.integration


@pytest.fixture
def mongo_client():
    uri = os.getenv("METADATA_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set METADATA_TEST_MONGO_URI to a dedicated replica set")
    database = "metadata_v1_test_" + uuid4().hex
    settings = Settings(
        _env_file=None, environment="test", mongo_uri=uri, mongo_database=database
    )
    try:
        with TestClient(create_app(settings)) as client:
            yield client, uri, database, settings
    finally:
        # Only the unique database created by this fixture is removed.
        assert database.startswith("metadata_v1_test_")
        with MongoClient(uri) as admin:
            admin.drop_database(database)


def test_real_persistence_indexes_and_publication(mongo_client):
    client, uri, database, settings = mongo_client
    seed(client)
    assert publish(client).status_code == 200
    with MongoClient(uri) as admin:
        collection = admin[database]["semantic_objects"]
        assert collection.count_documents({}) == 16
        doc = collection.find_one({"objectId": "src_product"})
        doc.pop("_id")
        with pytest.raises(DuplicateKeyError):
            collection.insert_one(doc)
        with pytest.raises(OperationFailure) as exc:
            collection.insert_one({"objectId": "broken"})
        assert exc.value.code == 121
    # A second app/client instance reads the same durable revision.
    with TestClient(create_app(settings)) as second:
        assert second.get(ROOT).json()["approvalStatus"] == "published"
        assert second.get(ROOT + "/measures/measure_error").status_code == 200


def test_real_transaction_rolls_back_invalid_mutation(mongo_client):
    client, _, _, _ = mongo_client
    seed(client)
    before = client.get(ROOT).json()["version"]
    payload = definition("dim_product")
    payload["definition"]["sourceId"] = "missing"
    response = client.put(
        ROOT + "/dimension-members/dim_product", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422
    assert client.get(ROOT).json()["version"] == before
    assert client.get(ROOT + "/dimension-members/dim_product").json()["version"] == 1


def test_concurrent_writers_cannot_silently_overwrite(mongo_client):
    client, _, _, _ = mongo_client
    seed(client)
    path = ROOT + "/dimension-members/dim_product"
    payload = definition("dim_product")

    def write(name):
        value = {**payload, "displayName": name}
        return client.put(path, json=value, headers={"If-Match": '"1"'}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["Products A", "Products B"]))
    assert sorted(results) == [200, 409]
    assert client.get(path).json()["version"] == 2


def test_publish_racing_with_object_mutation_is_consistent(mongo_client):
    client, _, _, _ = mongo_client
    seed(client)
    revision = client.get(ROOT).json()
    model = client.get("/api/v1/models/model_supply_planning").json()

    def publish_revision():
        return client.post(
            ROOT + "/publish",
            json={"expectedModelVersion": model["version"]},
            headers={"If-Match": str(revision["version"])},
        ).status_code

    def update_dimension():
        value = definition("dim_product")
        value["displayName"] = "Renamed product"
        return client.put(
            ROOT + "/dimension-members/dim_product", json=value, headers={"If-Match": '"1"'}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result()
            for future in [pool.submit(publish_revision), pool.submit(update_dimension)]
        ]
    assert sorted(results) == [200, 409]
    final = client.get(ROOT).json()
    assert (final["approvalStatus"] == "published") == (results[0] == 200)


def test_bootstrap_racing_clients_create_one_complete_default(mongo_client):
    client, uri, database, settings = mongo_client
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: client.post("/api/v1/workspace/bootstrap"), range(4))
        )
    assert all(response.status_code == 200 for response in results)
    assert {response.json()["modelId"] for response in results} == {"default_model"}
    with MongoClient(uri) as admin:
        assert admin[database].semantic_models.count_documents({}) == 1
        assert admin[database].semantic_revisions.count_documents({}) == 1
    with TestClient(create_app(settings)) as second:
        assert (
            second.get("/api/v1/models/default_model/revisions/revision_1").json()[
                "approvalStatus"
            ]
            == "draft"
        )


def test_reset_is_visible_across_application_instances(mongo_client):
    from backend.tests.fake_warehouse import FakeWarehouse

    client, _, _, settings = mongo_client
    seed(client)
    assert publish(client).status_code == 200
    with TestClient(create_app(settings, warehouse=FakeWarehouse())) as second:
        response = second.post("/api/v1/workspace/reset", json={"confirmed": True})
        assert response.status_code == 200, response.text
        models = client.get("/api/v1/models").json()["items"]
        assert len(models) == 1 and models[0]["modelId"] == "default_model"
        assert models[0]["publishedRevisionId"] is None
        assert client.get(ROOT).status_code == 404


def test_stable_table_registry_enforces_scope_and_physical_ownership(mongo_client):
    _, uri, database, _ = mongo_client
    with MongoClient(uri) as admin:
        resources = admin[database]["engine_table_resources"]
        resource = {
            "modelId": "model_test",
            "revisionId": "revision_1",
            "resourceKind": "dimension",
            "resourceId": "level_1",
            "physicalName": "__semantic_population_test",
            "storageOwner": "semanticTable:test",
            "columns": {},
        }
        resources.insert_one(dict(resource))
        with pytest.raises(DuplicateKeyError):
            resources.insert_one(
                {**resource, "physicalName": "__semantic_population_other"}
            )
        with pytest.raises(DuplicateKeyError):
            resources.insert_one({**resource, "revisionId": "revision_2"})
        resources.insert_one(
            {
                **resource,
                "revisionId": "revision_2",
                "physicalName": "__semantic_population_revision2",
            }
        )
        with pytest.raises(OperationFailure) as exc:
            resources.insert_one({"modelId": "model_invalid"})
        assert exc.value.code == 121
