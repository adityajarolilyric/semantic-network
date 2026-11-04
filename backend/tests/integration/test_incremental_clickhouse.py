# ruff: noqa: F811
from uuid import uuid4

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_measures_clickhouse import (
    BASE,
    setup,
    build_values,
    values,
    update_measure,
    measure,
)
from .test_managed_clickhouse import await_run

pytestmark = pytest.mark.integration


def incremental(client, mode="incremental", key=None):
    revision = client.get(BASE).json()
    return client.post(
        BASE + "/measures/execution/build",
        json={"mode": mode},
        headers={
            "If-Match": str(revision["version"]),
            "Idempotency-Key": key or uuid4().hex,
        },
    )


def run_incremental(client, mode="incremental"):
    response = incremental(client, mode)
    assert response.status_code == 202, response.text
    return await_run(client, BASE, response)


def calc(client, mid, dep="FQTY", factor=2):
    response = client.post(
        BASE + "/measures",
        json={
            "objectId": mid,
            "kind": "measure",
            "name": mid,
            "displayName": mid,
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativePlanningLevelId": "base",
                "calculationType": "AggregateFirst",
                "resultType": {"type": "number", "nullable": True},
                "formula": {
                    "displayText": f"{dep} * {factor}",
                    "expression": {
                        "type": "binary",
                        "operator": "multiply",
                        "left": {"type": "measureRef", "measureId": dep},
                        "right": {"type": "literal", "value": factor},
                    },
                },
            },
        },
    )
    assert response.status_code == 201, response.text


def patch_cell(client, mid="FQTY", value=99, level="base", members=None):
    keys = client.get(BASE + "/planning-levels/" + level).json()["definition"]["grain"][
        "keyIds"
    ]
    view = {
        "rows": [{"planningLevelId": level, "columnId": k} for k in keys],
        "columns": [],
        "measures": [mid],
    }
    context = client.post(BASE + "/planning-view/query", json=view)
    assert context.status_code == 200, context.text
    response = client.post(
        BASE + "/planning-view/edits",
        json={
            "view": view,
            "measureId": mid,
            "memberValues": members or ["P1", "L1"],
            "value": value,
            **context.json()["editContext"],
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    result = await_run(client, BASE, response)
    assert result["state"] == "succeeded", result


def column(client, mid, level="base"):
    result, columns = values(client, level)
    return [r[columns[mid]] for r in result["rows"]]


def test_incremental_noop_add_change_remove_preserves_edits_and_full_refresh_is_explicit(
    clickhouse_app, monkeypatch
):
    client, admin, database, _ = clickhouse_app
    setup(client)
    update_measure(
        client, "FQTY", lambda d: d.update(editable=True, allocation={"mode": "equal"})
    )
    assert build_values(client)["state"] == "succeeded"
    patch_cell(client)
    before, _ = values(client, "base")
    revision = client.get(BASE).json()["version"]
    queries = []
    original_query = type(admin).query
    original_command = type(admin).command

    def observe_query(connection, query=None, *args, **kwargs):
        if isinstance(query, str):
            queries.append(query)
        return original_query(connection, query, *args, **kwargs)

    def observe_command(connection, cmd=None, *args, **kwargs):
        if isinstance(cmd, str):
            queries.append(cmd)
        return original_command(connection, cmd, *args, **kwargs)

    monkeypatch.setattr(type(admin), "query", observe_query)
    monkeypatch.setattr(type(admin), "command", observe_command)
    noop = run_incremental(client)
    assert noop["state"] == "succeeded" and noop["reused"] is True, noop
    assert noop["generationId"] == before["generationId"]
    assert client.get(BASE).json()["version"] == revision
    assert not any("LIMIT 0" in q or q.lstrip().startswith("INSERT") for q in queries)
    assert column(client, "FQTY") == ["99.0", "60.0"]
    # A new calculated measure must read edited snapshot values, not source quantities.
    calc(client, "Double")
    queries.clear()
    built = run_incremental(client)
    assert built["state"] == "succeeded", built
    assert column(client, "FQTY") == ["99.0", "60.0"]
    assert column(client, "Double") == ["198.0", "120.0"]
    assert not any("ForecastValues" in q or "SalesValues" in q for q in queries)
    container = client.app.state.container

    async def steps():
        return await container.store.repository.list(
            "engine_run_steps", {"runId": built["runId"]}, key="stepId"
        )

    assert all(
        {"FQTY", "SQTY"} <= set(s["reusedMeasures"]) for s in client.portal.call(steps)
    )
    # Changing a formula invalidates its dependants but preserves referenced edits.
    update_measure(
        client, "Double", lambda d: d["formula"]["expression"]["right"].update(value=3)
    )
    calc(client, "Quadruple", "Double", 2)
    changed = run_incremental(client)
    assert changed["state"] == "succeeded", changed
    assert column(client, "Double") == ["297.0", "180.0"]
    assert column(client, "Quadruple") == ["594.0", "360.0"]
    assert column(client, "FQTY") == ["99.0", "60.0"]
    removed = client.get(BASE + "/measures/Quadruple").json()
    response = client.delete(
        BASE + "/measures/Quadruple", headers={"If-Match": str(removed["version"])}
    )
    assert response.status_code == 204, response.text
    assert run_incremental(client)["state"] == "succeeded"
    assert "Quadruple" not in values(client, "base")[1]
    assert column(client, "FQTY") == ["99.0", "60.0"]
    # A new referenced measure starts from its own source; it must not inherit
    # an edit from another measure that happens to reference the same column.
    measure(client, "Copy", "tx_forecast", "base", "col_3")
    assert run_incremental(client)["state"] == "succeeded"
    assert column(client, "FQTY") == ["99.0", "60.0"]
    assert column(client, "Copy") == ["40.0", "60.0"]
    assert column(client, "Double") == ["297.0", "180.0"]
    # Full Refresh retains the old explicit source-reload semantics.
    refreshed = run_incremental(client, "full")
    assert refreshed["state"] == "succeeded", refreshed
    assert column(client, "FQTY") == ["40.0", "60.0"]
    assert column(client, "Double") == ["120.0", "180.0"]
    assert (
        int(
            admin.command(
                "SELECT count() FROM system.tables WHERE database={db:String} AND engine='MergeTree' AND startsWith(comment,'semanticTable:')",
                parameters={"db": database},
            )
        )
        == 4
    )


def test_incremental_reuses_historical_captures_and_refuses_unprovable_baselines(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    setup(client)
    assert build_values(client)["state"] == "succeeded"
    container = client.app.state.container

    async def legacy(remove_inputs=False):
        repo = container.store.repository
        for lid in ["base", "pt"]:
            binding = await repo.get(
                "engine_artifact_bindings",
                {
                    "modelId": "default_model",
                    "revisionId": "revision_1",
                    "resourceKind": "measureValues",
                    "resourceId": lid,
                },
            )
            artifact = await repo.get(
                "engine_artifacts", {"artifactId": binding["artifactId"]}
            )
            artifact.pop("materialisationManifest", None)
            await repo.replace(
                "engine_artifacts", {"artifactId": artifact["artifactId"]}, artifact
            )
            if remove_inputs:
                while await repo.delete(
                    "engine_run_inputs", {"runId": artifact["runId"]}
                ):
                    pass

    client.portal.call(legacy)
    calc(client, "Double")
    assert run_incremental(client)["state"] == "succeeded"
    assert column(client, "Double") == ["80.0", "120.0"]
    before, _ = values(client, "base")
    client.portal.call(legacy, True)
    failed = incremental(client)
    assert failed.status_code == 409 and "Full Refresh" in failed.text
    assert values(client, "base")[0]["generationId"] == before["generationId"]


def test_registered_source_refresh_invalidates_only_its_measures_and_preserves_other_edits(
    clickhouse_app,
):
    client, admin, database, _ = clickhouse_app
    setup(client)
    update_measure(
        client, "FQTY", lambda d: d.update(editable=True, allocation={"mode": "equal"})
    )
    update_measure(
        client, "SQTY", lambda d: d.update(editable=True, allocation={"mode": "equal"})
    )
    assert build_values(client)["state"] == "succeeded"
    patch_cell(client)
    patch_cell(client, "SQTY", 300, "pt", ["P1"])
    assert column(client, "FQTY") == ["99.0", "60.0"]
    assert column(client, "SQTY", "pt") == ["300.0"]
    admin.command(
        f"ALTER TABLE `{database}`.`ForecastValues` UPDATE quantity=quantity*2 WHERE 1 SETTINGS mutations_sync=1"
    )
    source = next(
        o
        for o in client.get(BASE + "/sources").json()["items"]
        if o["name"] == "ForecastValues"
    )
    response = client.post(
        BASE + "/managed-tables/" + source["objectId"] + "/ensure-view",
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 202, response.text
    assert await_run(client, BASE, response)["state"] == "succeeded"
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    assert column(client, "FQTY") == ["80.0", "120.0"]
    assert column(client, "SQTY", "pt") == ["300.0"]
    assert column(client, "SQTY") == ["150.0", "150.0"]


def test_failed_incremental_candidates_do_not_activate_or_lose_edited_values(
    clickhouse_app, monkeypatch
):
    from backend.app.core.errors import MetadataError

    client, _, _, _ = clickhouse_app
    setup(client)
    update_measure(
        client, "FQTY", lambda d: d.update(editable=True, allocation={"mode": "equal"})
    )
    assert build_values(client)["state"] == "succeeded"
    patch_cell(client)
    before, _ = values(client, "base")
    calc(client, "Double")
    executor = client.app.state.container.measure_builds.executor
    original = executor.build

    async def fail(artifact, compiled, plan):
        result = await original(artifact, compiled, plan)
        if artifact["resourceId"] == "pt":
            raise MetadataError(
                "injected_failure", "Reject after both candidate levels were written."
            )
        return result

    monkeypatch.setattr(executor, "build", fail)
    failed = run_incremental(client)
    assert failed["state"] == "failed", failed
    after, columns = values(client, "base")
    assert after["generationId"] == before["generationId"]
    assert after["rows"] == before["rows"]
    assert "Double" not in columns
    monkeypatch.setattr(executor, "build", original)
    assert run_incremental(client)["state"] == "succeeded"
    assert column(client, "FQTY") == ["99.0", "60.0"]
    assert column(client, "Double") == ["198.0", "120.0"]


def test_new_aggregate_first_and_calculate_first_formulas_keep_their_grain_semantics(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    setup(client)
    update_measure(
        client, "FQTY", lambda d: d.update(editable=True, allocation={"mode": "equal"})
    )
    update_measure(
        client, "SQTY", lambda d: d.update(editable=True, allocation={"mode": "equal"})
    )
    assert build_values(client)["state"] == "succeeded"
    patch_cell(client)
    patch_cell(client, "SQTY", 180, "pt", ["P1"])
    for mid, kind, native in [
        ("AF", "AggregateFirst", "pt"),
        ("CF", "CalculateFirst", "base"),
    ]:
        expression = {
            "type": "call",
            "function": "abs",
            "arguments": [
                {
                    "type": "binary",
                    "operator": "subtract",
                    "left": {"type": "measureRef", "measureId": "FQTY"},
                    "right": {"type": "measureRef", "measureId": "SQTY"},
                }
            ],
        }
        d = {
            "measureType": "calculated",
            "nativePlanningLevelId": native,
            "calculationType": kind,
            "resultType": {"type": "number", "nullable": True},
            "formula": {"displayText": "ABS(FQTY-SQTY)", "expression": expression},
        }
        if kind == "CalculateFirst":
            d.update(
                aggregation={
                    "nonTime": "sum",
                    "time": "sum",
                    "nullInputPolicy": "ignore",
                    "allNullResult": "null",
                },
                allocation={"mode": "none"},
            )
        response = client.post(
            BASE + "/measures",
            json={
                "objectId": mid,
                "kind": "measure",
                "name": mid,
                "displayName": mid,
                "business": {},
                "definition": d,
            },
        )
        assert response.status_code == 201, response.text
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    assert column(client, "AF") == ["9.0", "30.0"]
    assert column(client, "AF", "pt") == ["21.0"]
    assert column(client, "CF") == ["9.0", "30.0"]
    assert column(client, "CF", "pt") == ["39.0"]
    assert column(client, "FQTY") == ["99.0", "60.0"]


def test_metadata_change_during_incremental_build_prevents_activation(
    clickhouse_app, monkeypatch
):
    client, _, _, _ = clickhouse_app
    setup(client)
    assert build_values(client)["state"] == "succeeded"
    before, _ = values(client, "base")
    calc(client, "Double")
    builds = client.app.state.container.measure_builds
    original = builds.executor.build
    changed = False

    async def change_revision(artifact, compiled, plan):
        nonlocal changed
        result = await original(artifact, compiled, plan)
        if not changed:
            changed = True
            await builds.store.transaction(
                lambda repo: builds.touch_revision(repo, "default_model", "revision_1")
            )
        return result

    monkeypatch.setattr(builds.executor, "build", change_revision)
    run = run_incremental(client)
    assert run["state"] in ("failed", "superseded"), run
    after, cols = values(client, "base")
    assert after["generationId"] == before["generationId"]
    assert after["rows"] == before["rows"] and "Double" not in cols
