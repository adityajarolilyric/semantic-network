# ruff: noqa: F811
import time
from uuid import uuid4

import pytest
from backend.tests.test_csv_sources import upload

from .test_csv_clickhouse import clickhouse_app  # noqa: F401

pytestmark = pytest.mark.integration


def await_run(client, base, response):
    assert response.status_code == 202, response.text
    run = response.json()
    for _ in range(200):
        run = client.get(base + "/engine/runs/" + run["runId"]).json()
        if run["state"] not in ("queued", "running"):
            return run
        time.sleep(0.05)
    raise AssertionError(run)


def setup(client, data):
    assert upload(client, data).status_code == 201
    client.post("/api/v1/workspace/bootstrap")
    base = "/api/v1/models/default_model/revisions/revision_1"
    run = await_run(
        client,
        base,
        client.post(
            base + "/managed-tables/enable",
            json={"tableName": "Forecast"},
            headers={"Idempotency-Key": str(uuid4())},
        ),
    )
    assert run["state"] == "succeeded", run
    return base, run["sourceId"]


def pivot():
    return {
        "primaryKeyColumnIds": ["col_1"],
        "pipeline": [
            {
                "stageId": "stage_main",
                "type": "pivot",
                "enabled": True,
                "groupByColumnIds": ["col_1"],
                "pairs": [
                    {
                        "pairId": "pair_main",
                        "attributeColumnId": "col_2",
                        "valueColumnId": "col_3",
                        "outputs": [
                            {"attributeValue": "FQTY", "name": "Forecast Quantity"},
                            {"attributeValue": "SQTY", "name": "Sales Quantity"},
                            {"attributeValue": "Missing", "name": "Expected"},
                        ],
                    }
                ],
            }
        ],
    }


def save(client, base, source, definition, key=None):
    current = client.get(base + "/managed-tables/" + source).json()
    return client.put(
        base + "/managed-tables/" + source,
        json=definition,
        headers={
            "If-Match": str(current["version"]),
            "Idempotency-Key": key or str(uuid4()),
        },
    )


def test_pivot_null_zero_missing_and_immutable_views(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    base, source = setup(
        client, b"product,attribute,value\nA,FQTY,0\nA,SQTY,\nB,FQTY,10\nC,Other,20\n"
    )
    raw_preview = client.get("/api/v1/warehouse/tables/Forecast/preview").json()
    before = admin.query(
        f"SELECT name FROM system.tables WHERE database='{database}' AND engine='View'"
    ).result_rows
    response = save(client, base, source, pivot(), "save_pivot")
    run = await_run(client, base, response)
    assert run["state"] == "succeeded", run
    managed = client.get(base + "/managed-tables/" + source).json()
    preview = client.get(base + "/managed-tables/" + source + "/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"] == [
        ["A", "0", None, None],
        ["B", "10", None, None],
        ["C", None, None, None],
    ]
    assert (
        len(
            admin.query(
                f"SELECT name FROM system.tables WHERE database='{database}' AND engine='View'"
            ).result_rows
        )
        == len(before) + 1
    )
    assert client.get("/api/v1/warehouse/tables/Forecast/preview").json() == raw_preview
    assert (
        client.get("/api/v1/warehouse/tables").json()["tables"][0]["name"] == "Forecast"
    )
    assert len(client.get("/api/v1/warehouse/tables").json()["tables"]) == 1
    # A true request replay returns the original run even after version advances.
    replay = client.put(
        base + "/managed-tables/" + source,
        json=pivot(),
        headers={"If-Match": "1", "Idempotency-Key": "save_pivot"},
    )
    assert replay.status_code == 202 and replay.json()["runId"] == run["runId"]
    changed = pivot()
    changed["pipeline"][0]["pairs"][0]["outputs"][0]["name"] = "Changed alias"
    assert (
        client.put(
            base + "/managed-tables/" + source,
            json=changed,
            headers={"If-Match": "1", "Idempotency-Key": "save_pivot"},
        ).status_code
        == 409
    )
    assert managed["definition"]["columns"][1]["nullable"] is True


@pytest.mark.parametrize("values", [b"1\nA,FQTY,1", b"\nA,FQTY,"])
def test_duplicate_cells_fail_and_keep_identity_view(clickhouse_app, values):
    client, _, _, _ = clickhouse_app
    base, source = setup(client, b"product,attribute,value\nA,FQTY," + values + b"\n")
    run = await_run(client, base, save(client, base, source, pivot()))
    assert run["state"] == "failed", run
    assert run["error"]["code"] == "duplicate_pivot_cell"
    current = client.get(base + "/managed-tables/" + source).json()
    assert current["version"] == 1 and current["definition"]["pipeline"] == []
    preview = client.get(base + "/managed-tables/" + source + "/preview")
    assert preview.status_code == 200 and len(preview.json()["rows"]) == 2


def test_identity_duplicates_optional_key_and_preview_checks(clickhouse_app):
    client, _, _, _ = clickhouse_app
    base, source = setup(client, b"product,attribute,value\nA,FQTY,1\nA,FQTY,1\n")
    proposed = client.post(
        base + "/managed-tables/" + source + "/preview", json=pivot()
    )
    assert (
        proposed.status_code == 422
        and proposed.json()["error"]["code"] == "duplicate_pivot_cell"
    )
    keyed = {"primaryKeyColumnIds": ["col_1"], "pipeline": []}
    run = await_run(client, base, save(client, base, source, keyed))
    assert run["error"]["code"] == "duplicate_managed_key"


def test_multiple_pairs_and_quoted_literals(clickhouse_app):
    client, _, _, _ = clickhouse_app
    base, source = setup(
        client,
        b'product,attribute,value,attribute_two,value_two\nA,"a,b",5,x,9\nA,"a\'b",6,y,8\n',
    )
    spec = pivot()
    spec["pipeline"][0]["pairs"][0]["outputs"] = [
        {"attributeValue": "a,b", "name": "Comma"},
        {"attributeValue": "a'b", "name": "Quote`name"},
    ]
    spec["pipeline"][0]["pairs"].append(
        {
            "pairId": "pair_two",
            "attributeColumnId": "col_4",
            "valueColumnId": "col_5",
            "outputs": [{"attributeValue": "x", "name": "Second"}],
        }
    )
    run = await_run(client, base, save(client, base, source, spec))
    assert run["state"] == "succeeded", run
    assert client.get(base + "/managed-tables/" + source + "/preview").json()[
        "rows"
    ] == [["A", "5", "6", "9"]]


def test_recover_candidate_created_before_ownership_comment(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    base, source_id = setup(client, b"product,attribute,value\nA,FQTY,3\n")
    container = client.app.state.container
    client.portal.call(container.engine_worker.stop)
    response = save(client, base, source_id, pivot())
    assert response.status_code == 202
    run = next(
        r
        for r in container.store.documents["engine_runs"]
        if r["runId"] == response.json()["runId"]
    )
    artifact = next(
        a
        for a in container.store.documents["engine_artifacts"]
        if a["artifactId"] == run["artifactId"]
    )
    source = client.get(base + "/sources/" + source_id).json()
    from backend.app.schemas.managed_tables import ManagedDefinition

    compiled = container.managed_tables.compiler.compile(
        container.managed_tables.planner.plan(
            source, ManagedDefinition.model_validate(pivot())
        )
    )
    admin.command(
        f"CREATE VIEW `{database}`.`{artifact['physicalName']}` AS {container.managed_tables.executor.artifact_query(artifact, compiled)}"
    )
    client.portal.call(container.engine_worker.start)
    result = await_run(client, base, response)
    assert result["state"] == "succeeded", result
    assert client.get(base + "/managed-tables/" + source_id + "/preview").json()[
        "rows"
    ] == [["A", "3", None, None]]


def test_publish_clone_and_dependency_protection(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    base, source = setup(client, b"product,attribute,value\nA,FQTY,10\n")
    response = save(client, base, source, pivot())
    assert await_run(client, base, response)["state"] == "succeeded"
    level = {
        "objectId": "pl_product",
        "kind": "planningLevel",
        "name": "Product",
        "displayName": "Product",
        "definition": {
            "population": {"operation": "source", "sourceId": source},
            "grain": {
                "keyIds": ["product"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "product",
                        "populationRef": {"columnId": "col_1"},
                    }
                ],
            },
        },
    }
    result = client.post(base + "/planning-levels", json=level)
    assert result.status_code == 201, result.text
    rev = client.get(base).json()
    update = client.put(
        base,
        json={"basePlanningLevelId": "pl_product", "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert update.status_code == 200, update.text
    current = client.get(base + "/managed-tables/" + source).json()
    assert (
        client.post(
            base + "/managed-tables/" + source + "/disable",
            headers={"If-Match": str(current["version"])},
        ).status_code
        == 422
    )
    # Referenced generated columns cannot be silently removed by another transform save.
    cid = current["definition"]["columns"][1]["columnId"]
    dim = {
        "objectId": "dim_product",
        "kind": "dimension",
        "name": "Product",
        "displayName": "Product",
        "isTimeDim": False,
        "definition": {
            "sourceId": source,
            "columns": [
                {
                    "columnId": "product",
                    "name": "product",
                    "displayName": "Product",
                    "sourceColumnId": "col_1",
                },
                {
                    "columnId": "forecast",
                    "name": "forecast",
                    "displayName": "Forecast",
                    "sourceColumnId": cid,
                },
            ],
            "keyColumnIds": ["product"],
            "labelColumnId": "product",
            "attributeColumnIds": ["forecast"],
        },
    }
    assert client.post(base + "/dimensions", json=dim).status_code == 201
    assert save(client, base, source, {"pipeline": []}).status_code == 422
    from .test_planning_clickhouse import build

    assert build(client, "pl_product")["state"] == "succeeded"
    before = client.get(base).json()
    model = client.get("/api/v1/models/default_model").json()
    published = client.post(
        base + "/publish",
        json={"expectedModelVersion": model["version"]},
        headers={"If-Match": str(before["version"])},
    )
    assert published.status_code == 200, published.text
    frozen = published.json()
    # A repair changes only an operational binding, never published semantic metadata.
    repair = client.post(
        base + "/managed-tables/" + source + "/ensure-view",
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert await_run(client, base, repair)["state"] == "succeeded"
    assert client.get(base).json() == frozen
    model = client.get("/api/v1/models/default_model").json()
    clone = client.post(
        "/api/v1/models/default_model/revisions",
        json={
            "revisionId": "draft_2",
            "expectedModelVersion": model["version"],
            "parentRevisionId": "revision_1",
            "metadataContractVersion": 2,
        },
    )
    assert clone.status_code == 201, clone.text
    clone_base = base.replace("revision_1", "draft_2")
    assert (
        client.get(clone_base + "/managed-tables/" + source).json()["definition"]
        == current["definition"]
    )
    assert (
        client.get(clone_base + "/managed-tables/" + source + "/preview").status_code
        == 404
    )
    run = await_run(
        client,
        clone_base,
        client.post(
            clone_base + "/managed-tables/" + source + "/ensure-view",
            headers={"Idempotency-Key": str(uuid4())},
        ),
    )
    assert run["state"] == "succeeded", run
    assert (
        client.get(clone_base + "/managed-tables/" + source + "/preview").status_code
        == 200
    )
    assert client.get(base).json() == frozen
    # Raw tables are unchanged through publication, repair and clone.
    assert admin.command(f"SELECT count() FROM `{database}`.Forecast") == 1


def test_discover_saved_managed_output_columns(clickhouse_app):
    client, _, _, _ = clickhouse_app
    base, source = setup(
        client, b"product,attribute,value\nA,FQTY,0\nB,FQTY,10\nC,SQTY,20\n"
    )
    run = await_run(client, base, save(client, base, source, pivot()))
    assert run["state"] == "succeeded", run
    managed = client.get(base + "/managed-tables/" + source).json()
    cid = managed["definition"]["pipeline"][0]["pairs"][0]["outputs"][0]["columnId"]
    path = base + "/managed-tables/" + source + "/attribute-values"
    response = client.get(path, params={"columnId": cid, "managedOutput": True})
    assert response.status_code == 200, response.text
    assert set(response.json()["values"]) == {0, 10, None}
    page = client.get(path, params={"columnId": cid, "managedOutput": True, "limit": 1})
    assert page.status_code == 200, page.text
    assert len(page.json()["values"]) == 1
    assert page.json()["nextOffset"] == 1
    assert page.json()["artifactId"] == response.json()["artifactId"]
    raw = client.get(path, params={"columnId": "col_2"})
    assert raw.status_code == 200, raw.text
    assert set(raw.json()["values"]) == {"FQTY", "SQTY"}
    missing = client.get(path, params={"columnId": "col_2", "managedOutput": True})
    assert missing.status_code == 422, missing.text
