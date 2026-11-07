from copy import deepcopy
from backend.tests.conftest import ROOT, object_payload
from backend.scripts.migrate_dimensions import migrate_objects, OLD_GRAIN, convert
from backend.app.schemas.objects import OBJECT_DOCUMENT


def test_source_dimension_is_one_mongo_aggregate_and_its_dependents_protect_deletion(
    populated,
):
    client = populated
    catalogs = client.get(ROOT + "/dimension-members").json()["items"]
    catalog = catalogs[0]
    root = client.post(
        ROOT + "/dimension-members/" + catalog["objectId"] + "/grain"
    ).json()
    assert root["kind"] == "dimension"
    assert root["memberCatalogId"] == catalog["objectId"]
    assert "sourceId" in root["memberCatalog"]
    assert not {"kind", "objectId", "name"} & root["memberCatalog"].keys()
    stored = client.portal.call(
        lambda: client.app.state.container.store.repository.list(
            "semantic_objects", {}, key="objectId", limit=100
        )
    )
    assert not any(obj["kind"] == "dimensionMembers" for obj in stored)
    body = object_payload(catalog)
    body["description"] = "Changed atomically"
    response = client.put(
        ROOT + "/dimension-members/" + catalog["objectId"],
        json=body,
        headers={"If-Match": str(catalog["version"])},
    )
    assert response.status_code == 200, response.text
    updated = client.get(ROOT + "/dimensions/" + root["objectId"]).json()
    assert updated["version"] == root["version"] + 1
    assert updated["description"] == "Changed atomically"
    response = client.delete(
        ROOT + "/dimensions/" + root["objectId"],
        headers={"If-Match": str(updated["version"])},
    )
    assert response.status_code == 422, response.text
    assert client.get(ROOT + "/dimensions/" + root["objectId"]).status_code == 200


def test_one_time_migration_absorbs_members_and_renames_references(populated):
    catalog = populated.get(ROOT + "/dimension-members").json()["items"][0]
    # The legacy shape exists only in this migration input, never in application contracts.
    legacy = deepcopy(catalog)
    legacy["kind"] = "dimension"
    migrated = migrate_objects([legacy])
    assert len(migrated) == 1
    assert migrated[0]["kind"] == "dimension"
    assert migrated[0]["memberCatalogId"] == catalog["objectId"]
    assert migrated[0]["memberCatalog"]["columns"] == catalog["definition"]["columns"]
    OBJECT_DOCUMENT.validate_python(migrated[0])
    before = {
        "base" + OLD_GRAIN[0].upper() + OLD_GRAIN[1:] + "Id": "base",
        "definition": {OLD_GRAIN + "Id": "native", "dimensionId": "members"},
    }
    assert convert(before) == {
        "baseDimensionId": "base",
        "definition": {"dimensionId": "native", "memberCatalogId": "members"},
    }


def test_unchanged_dimension_save_preserves_metadata_versions(populated):
    client = populated
    dimension = client.get(ROOT + "/dimensions").json()["items"][0]
    before = client.get(ROOT).json()["version"]
    response = client.put(
        ROOT + "/dimensions/" + dimension["objectId"],
        json=object_payload(dimension),
        headers={"If-Match": str(dimension["version"])},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == dimension["version"]
    assert client.get(ROOT).json()["version"] == before
