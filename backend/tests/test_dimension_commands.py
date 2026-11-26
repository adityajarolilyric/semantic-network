from copy import deepcopy

from .conftest import ROOT, object_payload


def derived(populated):
    document = next(
        obj
        for obj in populated.get(ROOT + "/dimensions").json()["items"]
        if obj["definition"]["population"]["operation"] == "distinct_projection"
    )
    payload = object_payload(document)
    payload.update(
        objectId="saved_projection",
        name="Saved projection",
        displayName="Saved projection",
    )
    return payload


def command(client, payload, key="save_dimension", version=None):
    return client.post(
        ROOT + "/dimensions/save-and-build",
        json={"definition": payload, "expectedVersion": version},
        headers={"Idempotency-Key": key},
    )


def test_derived_save_is_idempotent_scoped_and_cancellable(populated):
    client = populated
    client.portal.call(client.app.state.container.engine_worker.stop)
    payload = derived(client)
    response = command(client, payload)
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["resourceKind"] == "dimensionSet"
    assert command(client, payload).json()["runId"] == run["runId"]
    assert client.get(ROOT + "/dimensions/saved_projection").json()["version"] == 1
    repo = client.app.state.container.store.repository

    async def capture_and_child():
        parent = await repo.get("engine_runs", {"runId": run["runId"]})
        assert parent["populationOnly"] is True
        assert parent["dimensionIds"][-1] == "saved_projection"
        assert set(parent["dimensionIds"]) == {
            payload["definition"]["population"]["sourceDimensionId"],
            "saved_projection",
        }
        child = {
            **deepcopy(parent),
            "runId": "population_child",
            "resourceKind": "dimension",
            "idempotencyKey": "population_child",
        }
        await repo.insert("engine_runs", child)
        await repo.replace(
            "engine_runs",
            {"runId": parent["runId"]},
            {**parent, "childRunId": child["runId"]},
        )

    client.portal.call(capture_and_child)
    assert (
        client.post(ROOT + "/engine/runs/" + run["runId"] + "/cancel").json()["state"]
        == "cancelled"
    )
    assert (
        client.get(ROOT + "/engine/runs/population_child").json()["state"]
        == "cancelled"
    )
    changed = {**payload, "name": "Different"}
    assert command(client, changed).status_code == 409
    assert command(client, payload, key="stale_save", version=97).status_code == 409


def test_source_dimension_uses_metadata_save_without_build_command(populated):
    source_dimension = next(
        obj
        for obj in populated.get(ROOT + "/dimensions").json()["items"]
        if obj["definition"]["population"]["operation"] != "distinct_projection"
    )
    payload = object_payload(source_dimension)
    payload["definition"]["population"] = {
        "operation": "source",
        "sourceId": "not_used",
    }
    response = command(populated, payload)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "source_dimension_save"
