from copy import deepcopy

from backend.app.repositories.metadata.catalog import ObjectRepository
from .conftest import ROOT, object_payload


def replacement_dimension(client):
    payload = object_payload(client.get(ROOT + "/dimensions/pl_plt").json())
    payload.update(
        objectId="alternate_plt", name="Alternate PLT", displayName="Alternate PLT"
    )
    response = client.post(ROOT + "/dimensions", json=payload)
    assert response.status_code == 201, response.text
    return payload["objectId"]


def remap(client, target):
    old = client.get(ROOT + "/transactions/tx_forecast").json()
    payload = object_payload(old)
    payload["definition"]["dimensionId"] = target
    return old, payload


def save(client, old, payload, expected=None):
    return client.put(
        ROOT + "/transactions/tx_forecast",
        json=payload,
        headers={"If-Match": str(expected if expected is not None else old["version"])},
    )


def test_dimension_change_refreshes_all_owned_references_atomically(populated):
    client = populated
    target = replacement_dimension(client)
    extra = object_payload(client.get(ROOT + "/measures/measure_fqty").json())
    extra.update(
        objectId="other_fqty", name="Other Forecast", displayName="Other Forecast"
    )
    assert client.post(ROOT + "/measures", json=extra).status_code == 201
    label = deepcopy(extra)
    label.update(
        objectId="product_label", name="Product label", displayName="Product label"
    )
    label["definition"]["valueColumnId"] = client.get(
        ROOT + "/sources/src_forecast"
    ).json()["definition"]["columns"][0]["columnId"]
    label["definition"]["aggregation"].update(
        nonTime="single_value", time="single_value"
    )
    label["definition"].update(editable=False, allocation=None)
    response = client.post(ROOT + "/measures", json=label)
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["resultType"]["type"] == "string"
    before = client.get(ROOT + "/measures").json()["items"]
    revision = client.get(ROOT).json()
    old, payload = remap(client, target)
    response = save(client, old, payload)
    assert response.status_code == 200, response.text
    updated = {m["objectId"]: m for m in client.get(ROOT + "/measures").json()["items"]}
    for measure in before:
        current = updated[measure["objectId"]]
        if measure["definition"].get("transactionId") == old["objectId"]:
            assert current["definition"]["transactionId"] == old["objectId"]
            assert "nativeDimensionId" not in current["definition"]
            assert current["version"] == measure["version"] + 1
            expected = deepcopy(measure["definition"])
            assert current["definition"] == expected
            assert current["objectId"] == measure["objectId"]
            assert current["name"] == measure["name"]
            assert current["business"] == measure["business"]
            assert current["createdAt"] == measure["createdAt"]
        else:
            assert current == measure
    assert client.get(ROOT).json()["version"] == revision["version"] + 1
    # The original measure ETag cannot overwrite the automatic update.
    old_measure = next(m for m in before if m["objectId"] == "measure_fqty")
    stale = client.put(
        ROOT + "/measures/measure_fqty",
        json=object_payload(old_measure),
        headers={"If-Match": str(old_measure["version"])},
    )
    assert stale.status_code == 409, stale.text


def test_invalid_mapping_rolls_back_transaction_measures_and_revision(populated):
    target = replacement_dimension(populated)
    old, payload = remap(populated, target)
    payload["definition"]["keyMappings"] = []
    revision = populated.get(ROOT).json()
    measures = populated.get(ROOT + "/measures").json()
    response = save(populated, old, payload)
    assert response.status_code == 422, response.text
    assert populated.get(ROOT).json() == revision
    assert populated.get(ROOT + "/transactions/tx_forecast").json() == old
    assert populated.get(ROOT + "/measures").json() == measures


def test_related_measure_write_conflict_rolls_back_all_changes(populated, monkeypatch):
    target = replacement_dimension(populated)
    first = object_payload(populated.get(ROOT + "/measures/measure_fqty").json())
    first.update(objectId="a_first_measure", name="First", displayName="First")
    assert populated.post(ROOT + "/measures", json=first).status_code == 201
    old, payload = remap(populated, target)
    revision = populated.get(ROOT).json()
    measures = populated.get(ROOT + "/measures").json()
    replace = ObjectRepository.replace

    async def conflict(self, lookup, document):
        if document["objectId"] == "measure_fqty":
            return False
        return await replace(self, lookup, document)

    monkeypatch.setattr(ObjectRepository, "replace", conflict)
    response = save(populated, old, payload)
    assert response.status_code == 409, response.text
    assert populated.get(ROOT).json() == revision
    assert populated.get(ROOT + "/transactions/tx_forecast").json() == old
    assert populated.get(ROOT + "/measures").json() == measures


def test_other_transaction_edits_leave_measure_versions_unchanged(populated):
    old = populated.get(ROOT + "/transactions/tx_forecast").json()
    payload = object_payload(old)
    payload["description"] = "Updated description"
    measures = populated.get(ROOT + "/measures").json()
    assert save(populated, old, payload).status_code == 200
    assert populated.get(ROOT + "/measures").json() == measures
