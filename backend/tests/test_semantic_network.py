from copy import deepcopy
from backend.tests.conftest import ROOT


def test_build_command_is_idempotent_and_cancel_cancels_its_child(populated):
    client = populated
    container = client.app.state.container
    client.portal.call(container.engine_worker.stop)
    revision = client.get(ROOT).json()
    headers = {
        "If-Match": str(revision["version"]),
        "Idempotency-Key": "network_command",
    }
    response = client.post(
        ROOT + "/measures/execution/build",
        json={"mode": "incremental"},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["runId"]
    repeated = client.post(
        ROOT + "/measures/execution/build",
        json={"mode": "incremental"},
        headers=headers,
    )
    assert repeated.json()["runId"] == run_id
    repo = container.store.repository

    async def child():
        parent = await repo.get("engine_runs", {"runId": run_id})
        queued = {
            **deepcopy(parent),
            "runId": "child_values",
            "resourceKind": "measureSet",
            "idempotencyKey": run_id + "_values",
        }
        await repo.insert("engine_runs", queued)
        await repo.replace(
            "engine_runs", {"runId": run_id}, {**parent, "childRunId": queued["runId"]}
        )

    client.portal.call(child)
    cancelled = client.post(ROOT + "/engine/runs/" + run_id + "/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert client.get(ROOT + "/engine/runs/child_values").json()["state"] == "cancelled"
