from .conftest import ROOT
from .test_metadata_api import publish


def command(client, oid="NewTransaction"):
    source = client.get(ROOT + "/sources/src_forecast").json()
    return {
        "objectId": oid,
        "name": oid,
        "displayName": oid,
        "sourceId": source["objectId"],
        "grainColumnIds": [c["columnId"] for c in source["definition"]["columns"][:3]],
    }


def test_creation_keeps_base_and_retries_without_extra_versions(populated):
    payload = command(populated)
    original = populated.get(ROOT).json()
    result = populated.post(ROOT + "/transactions/from-grain", json=payload)
    assert result.status_code == 201, result.text
    created = result.json()
    assert (
        populated.get(ROOT).json()["basePlanningLevelId"]
        == original["basePlanningLevelId"]
    )
    assert created["transaction"]["definition"]["mappingMode"] == "direct"
    assert "createPlanningLevel" not in created["transaction"]["definition"]
    revision = populated.get(ROOT).json()
    replay = populated.post(ROOT + "/transactions/from-grain", json=payload)
    assert replay.json() == created
    assert populated.get(ROOT).json() == revision


def test_object_limit_prevents_partial_creation(populated):
    service = populated.app.state.container.transaction_grains
    objects = []
    for kind in [
        "sources",
        "dimensions",
        "planning-levels",
        "transactions",
        "measures",
    ]:
        objects.extend(populated.get(ROOT + "/" + kind).json()["items"])
    service.max_objects = len(objects) + 1
    before = populated.get(ROOT).json()
    result = populated.post(ROOT + "/transactions/from-grain", json=command(populated))
    assert result.status_code == 409, result.text
    assert populated.get(ROOT).json() == before
    assert populated.get(ROOT + "/transactions/NewTransaction").status_code == 404


def test_published_revision_cannot_generate_a_level(populated):
    payload = command(populated)
    assert publish(populated).status_code == 200
    before = populated.get(ROOT).json()
    result = populated.post(ROOT + "/transactions/from-grain", json=payload)
    assert result.status_code == 409, result.text
    assert populated.get(ROOT).json() == before


def test_grain_creation_preserves_transaction_filters_without_filtering_population(
    populated,
):
    payload = command(populated, "FilteredForecast")
    payload["filters"] = {
        "root": {
            "type": "group",
            "combinator": "and",
            "children": [
                {
                    "type": "condition",
                    "columnId": payload["grainColumnIds"][0],
                    "operator": "eq",
                    "values": ["P1"],
                }
            ],
        }
    }
    response = populated.post(ROOT + "/transactions/from-grain", json=payload)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["transaction"]["definition"]["filters"] == payload["filters"]
    assert created["planningLevel"]["definition"].get("filters") is None
    assert "filters" not in created["planningLevel"]["definition"]["population"]
    assert (
        populated.post(ROOT + "/transactions/from-grain", json=payload).json()
        == created
    )


def test_invalid_onboarding_filter_does_not_save_partial_objects(populated):
    payload = command(populated, "InvalidFilteredForecast")
    payload["filters"] = {
        "root": {
            "type": "group",
            "combinator": "and",
            "children": [
                {
                    "type": "condition",
                    "columnId": "missing",
                    "operator": "eq",
                    "values": ["P1"],
                }
            ],
        }
    }
    before = populated.get(ROOT).json()
    response = populated.post(ROOT + "/transactions/from-grain", json=payload)
    assert response.status_code == 422, response.text
    assert populated.get(ROOT).json() == before
    assert (
        populated.get(ROOT + "/transactions/InvalidFilteredForecast").status_code == 404
    )
