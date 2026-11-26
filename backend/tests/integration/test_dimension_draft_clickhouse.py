# ruff: noqa: F811
from copy import deepcopy
from uuid import uuid4
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build, level, table
from .test_managed_clickhouse import await_run

pytestmark = pytest.mark.integration


def preview_build(client, payload, version):
    return await_run(
        client,
        BASE,
        client.post(
            BASE + "/dimensions/editor/draft/build",
            json={"definition": payload, "expectedVersion": version},
            headers={"Idempotency-Key": uuid4().hex},
        ),
    )


def test_source_draft_preview_and_save_preserve_metadata_without_copy_tables(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    source = managed(client, "Population", b"product,category\nP1,A\nP2,B\n")
    payload = level(source)
    original = create(client, payload)
    assert build(client, "pl_main")["state"] == "succeeded"
    before = table(client, "pl_main")
    revision = client.get(BASE).json()
    changed = {**deepcopy(payload), "name": "Changed level"}
    preview = preview_build(client, changed, 1)
    assert preview["state"] == "succeeded", preview
    assert client.get(BASE + "/dimensions/pl_main").json() == original
    assert client.get(BASE).json() == revision
    assert table(client, "pl_main")["rows"] == before["rows"]
    candidate = client.get(
        BASE + "/dimensions/editor/draft/" + preview["runId"] + "/preview"
    )
    assert candidate.status_code == 200, candidate.text
    assert (
        candidate.json()["state"] == "preview"
        and candidate.json()["rows"] == before["rows"]
    )
    names = admin.query(
        "SELECT name FROM system.tables WHERE database={db:String} AND startsWith(comment,'semanticCurrent:')",
        parameters={"db": database},
    ).result_rows
    assert names == []
    committed = client.post(
        BASE + "/dimensions/editor/draft/commit",
        json={"definition": changed, "runId": preview["runId"]},
    )
    assert committed.status_code == 200, committed.text
    assert (
        committed.json()["version"] == 2 and committed.json()["name"] == "Changed level"
    )
    assert table(client, "pl_main")["rows"] == before["rows"]
    assert not admin.query("SELECT name FROM system.tables WHERE database={db:String} AND startsWith(comment,'semanticCurrent:')", parameters={"db": database}).result_rows
    assert (
        client.post(
            BASE + "/dimensions/editor/draft/commit",
            json={"definition": changed, "runId": preview["runId"]},
        ).json()
        == committed.json()
    )


def test_new_preview_is_not_saved_and_commit_refuses_changed_payload(clickhouse_app):
    client, _, _, _ = clickhouse_app
    source = managed(client, "Population", b"product,category\nP1,A\n")
    payload = level(source)
    preview = preview_build(client, payload, None)
    assert preview["state"] == "succeeded", preview
    assert client.get(BASE + "/dimensions/pl_main").status_code == 404
    bad = client.post(
        BASE + "/dimensions/editor/draft/commit",
        json={
            "definition": {**payload, "name": "Different"},
            "runId": preview["runId"],
        },
    )
    assert bad.status_code == 409
    assert client.get(BASE + "/dimensions/pl_main").status_code == 404
    good = client.post(
        BASE + "/dimensions/editor/draft/commit",
        json={"definition": payload, "runId": preview["runId"]},
    )
    assert good.status_code == 200, good.text
    assert good.json()["version"] == 1
    assert table(client, "pl_main")["rows"] == [["P1", "A"]]
