# ruff: noqa: F811
from uuid import uuid4
import time
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_decision_view_clickhouse import seed_view, field, query_view
from .test_measures_clickhouse import BASE, build_values, values, update_measure
from .test_managed_clickhouse import await_run

pytestmark = pytest.mark.integration


def editable(client, mid="FQTY", mode="equal", **patch):
    update_measure(
        client,
        mid,
        lambda d: d.update({"editable": True, "allocation": {"mode": mode}, **patch}),
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run


def edit(
    client,
    mid="FQTY",
    value=200,
    members=("A", "Z1"),
    rows=None,
    context=None,
    key=None,
):
    rows = (
        rows
        if rows is not None
        else [field("Product", "brand"), field("Location", "zone")]
    )
    view = {"rows": rows, "columns": [], "measures": [mid]}
    if context is None:
        response = query_view(client, rows=rows, measures=[mid])
        assert response.status_code == 200, response.text
        context = response.json()["editContext"]
    return client.post(
        BASE + "/decision-view/edits",
        json={
            "view": view,
            "measureId": mid,
            "memberValues": list(members),
            "value": value,
            **context,
        },
        headers={"Idempotency-Key": key or uuid4().hex},
    )


def test_edit_coarser_equal_base_first_formulas_rebuild_and_two_tables(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    seed_view(client)
    editable(client)
    context = query_view(
        client,
        rows=[field("Product", "brand"), field("Location", "zone")],
        measures=["FQTY"],
    ).json()
    assert context["measures"][0]["editable"]
    run = await_run(client, BASE, edit(client))
    assert run["state"] == "succeeded", run
    result, cols = values(client, "base")
    assert [r[cols["FQTY"]] for r in result["rows"]] == [
        "100.0",
        "30.0",
        "100.0",
        "-10.0",
    ]
    # Unaffected AVG/reference values are preserved; dependent formulas are recomputed.
    assert [r[cols["AVGF"]] for r in result["rows"]] == [
        "10.0",
        "30.0",
        "100.0",
        "-10.0",
    ]
    assert [r[cols["ErrorCF"]] for r in result["rows"]] == [
        "40.0",
        "30.0",
        "20.0",
        "30.0",
    ]
    result, cols = values(client, "pt")
    assert [r[cols["FQTY"]] for r in result["rows"]] == ["130.0", "100.0", "-10.0"]
    stale = edit(client, context=context["editContext"])
    assert stale.status_code == 409, stale.text
    count = int(
        admin.command(
            "SELECT count() FROM system.tables WHERE database={db:String} AND engine='MergeTree' AND startsWith(comment,'semanticCurrent:')",
            parameters={"db": database},
        )
    )
    documents = client.app.state.container.store.documents
    active_ids = {b["artifactId"] for b in documents["engine_artifact_bindings"]}
    current_tables = {
        a["physicalName"]
        for a in documents["engine_artifacts"]
        if a["artifactId"] in active_ids
        and a.get("kind") == "table"
        and a.get("storageMode") == "current"
    }
    # Run completion commits before idempotent post-commit clone cleanup.
    for _ in range(40):
        count = int(
            admin.command(
                "SELECT count() FROM system.tables WHERE database={db:String} AND engine='MergeTree' AND startsWith(comment,'semanticCurrent:')",
                parameters={"db": database},
            )
        )
        if count == len(current_tables):
            break
        time.sleep(0.05)
    assert count == len(current_tables)
    # No replay: rebuilding reads the original transactions again.
    assert build_values(client)["state"] == "succeeded"
    result, cols = values(client, "base")
    assert [r[cols["FQTY"]] for r in result["rows"]] == [
        "10.0",
        "30.0",
        "100.0",
        "-10.0",
    ]


@pytest.mark.parametrize(
    "mode,patch,expected",
    [
        ("none", {}, ["220.0", "30.0", "220.0", "-10.0"]),
        ("weighted_by_self", {}, ["20.0", "30.0", "200.0", "-10.0"]),
        (
            "weighted",
            {"allocation": {"mode": "weighted", "basisMeasureId": "AVGF"}},
            ["20.0", "30.0", "200.0", "-10.0"],
        ),
    ],
)
def test_edit_disaggregation_uses_configured_policy_and_snapshot_weights(
    clickhouse_app, mode, patch, expected
):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client, mode=mode, **patch)
    response = edit(client, value=220)
    run = await_run(client, BASE, response)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "base")
    assert [r[cols["FQTY"]] for r in data["rows"]] == expected
    assert (
        client.portal.call(
            client.app.state.container.store.repository.get,
            "engine_run_inputs",
            {"runId": run["runId"], "inputId": "__edit"},
        )
        is None
    )


def test_edit_native_average_recomputes_the_complete_coarser_group(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client, "AVGF", "none")
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="AVGF",
            value=50,
            rows=[field("Product", "product"), field("Location", "location")],
            members=["P1", "L1"],
        ),
    )
    assert run["state"] == "succeeded", run
    data, cols = values(client, "pt")
    assert [r[cols["AVGF"]] for r in data["rows"]] == ["40.0", "100.0", "-10.0"]
    # A second edit starts from the current Base, preserving the first change.
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="AVGF",
            value=70,
            rows=[field("Product", "product"), field("Location", "location")],
            members=["P2", "L1"],
        ),
    )
    assert run["state"] == "succeeded", run
    data, cols = values(client, "base")
    assert [r[cols["AVGF"]] for r in data["rows"]] == ["50.0", "30.0", "70.0", "-10.0"]


def test_edit_permissions_are_based_on_displayed_grain_and_server_validated(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client, "SQTY", "none")
    finer = query_view(
        client,
        rows=[field("Product", "product"), field("Location", "location")],
        measures=["SQTY"],
    )
    assert finer.status_code == 200, finer.text
    assert not finer.json()["measures"][0]["editable"]
    rejected = edit(
        client,
        mid="SQTY",
        rows=[field("Product", "product"), field("Location", "location")],
        members=["P1", "L1"],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "measure_not_editable"
    assert edit(client, mid="FQTY").status_code == 422
    assert edit(client, mid="ErrorAF").status_code == 422
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="SQTY",
            value=150,
            rows=[field("Product", "product")],
            members=["P1"],
        ),
    )
    assert run["state"] == "succeeded", run
    data, cols = values(client, "pt")
    assert data["rows"][0][cols["SQTY"]] == "300.0"  # Repeat + SUM is intentional.


def test_edit_ad_hoc_global_grain_and_idempotency(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client)
    context = query_view(client, rows=[], measures=["FQTY"]).json()["editContext"]
    key = uuid4().hex
    response = edit(client, value=400, rows=[], members=[], context=context, key=key)
    run = await_run(client, BASE, response)
    assert run["state"] == "succeeded", run
    replay = edit(client, value=400, rows=[], members=[], context=context, key=key)
    assert replay.status_code == 202, replay.text
    assert replay.json()["runId"] == run["runId"]
    data, cols = values(client, "base")
    assert {r[cols["FQTY"]] for r in data["rows"]} == {"100.0"}
    invalid_generation = {
        **query_view(client, measures=["FQTY"]).json()["editContext"],
        "generationId": context["generationId"],
    }
    assert (
        edit(client, rows=[], members=[], context=invalid_generation).status_code == 409
    )


def test_failed_edit_keeps_every_prior_binding_and_discards_transient_context(
    clickhouse_app, monkeypatch
):
    from backend.app.core.errors import MetadataError

    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client)
    before = query_view(client, measures=["FQTY"]).json()
    executor = client.app.state.container.measure_builds.executor
    original = executor.build

    async def fail_second(artifact, compiled, plan):
        if artifact["resourceId"] == "pt":
            raise MetadataError(
                "injected_failure", "Simulated downstream build failure"
            )
        return await original(artifact, compiled, plan)

    monkeypatch.setattr(executor, "build", fail_second)
    run = await_run(client, BASE, edit(client))
    assert run["state"] == "failed", run
    after = query_view(client, measures=["FQTY"]).json()
    assert after["editContext"] == before["editContext"]
    assert after["rows"] == before["rows"]
    assert (
        client.portal.call(
            client.app.state.container.store.repository.get,
            "engine_run_inputs",
            {"runId": run["runId"], "inputId": "__edit"},
        )
        is None
    )


def test_independent_native_measure_can_define_disaggregation_and_survives_other_edits(
    clickhouse_app,
):
    from .test_planning_clickhouse import managed, create, build
    from .test_decision_view_clickhouse import dimension
    from .test_measures_clickhouse import transaction, measure

    client, _, _, _ = clickhouse_app
    seed_view(client)
    source = managed(
        client, "IndependentFacts", b"item,category,quantity\nX,All,17\nY,All,23\n"
    )
    dimension(client, "Item", source, ["item", "category"])
    create(
        client,
        {
            "objectId": "independent",
            "kind": "dimension",
            "name": "Independent",
            "displayName": "Independent",
            "definition": {
                "population": {"operation": "source", "sourceId": source},
                "populationAttributes": [
                    {
                        "columnId": "category",
                        "name": "category",
                        "populationRef": {"columnId": "col_2"},
                    }
                ],
                "grain": {
                    "keyIds": ["item"],
                    "columns": [
                        {
                            "grainColumnId": "item",
                            "name": "item",
                            "populationRef": {"columnId": "col_1"},
                        }
                    ],
                },
                "memberBindings": [
                    {
                        "memberCatalogId": "Item",
                        "keyMappings": [
                            {"grainColumnId": "item", "memberColumnId": "item"}
                        ],
                        "selectedAttributeColumnIds": [],
                    }
                ],
            },
        },
    )
    transaction(client, "other_tx", source, "independent", [("item", "col_1")])
    measure(client, "IndependentQty", "other_tx", "independent", "col_3", mode="equal")
    update_measure(client, "IndependentQty", lambda d: d.update(editable=True))
    assert build(client, "independent")["state"] == "succeeded"
    create(
        client,
        {
            "objectId": "independent_group",
            "kind": "dimension",
            "name": "Independent category",
            "displayName": "Independent category",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourceDimensionId": "independent",
                },
                "grain": {
                    "keyIds": ["category"],
                    "columns": [
                        {
                            "grainColumnId": "category",
                            "name": "category",
                            "populationRef": {"sourceOutputColumnId": "category"},
                        }
                    ],
                },
            },
        },
    )
    assert build(client, "independent_group")["state"] == "succeeded"
    transaction(
        client,
        "independent_group_tx",
        source,
        "independent_group",
        [("category", "col_2")],
    )
    editable(client)
    data, cols = values(client, "base")
    assert "IndependentQty" not in cols
    data, cols = values(client, "independent")
    assert [r[cols["IndependentQty"]] for r in data["rows"]] == ["17.0", "23.0"]
    query = query_view(
        client, rows=[field("Item", "item")], measures=["IndependentQty"]
    )
    assert query.status_code == 200, query.text
    assert query.json()["plan"]["nativeOnly"]
    assert query.json()["measures"][0]["editable"]
    assert query.json()["measures"][0]["writeLevelId"] == "independent"
    run = await_run(client, BASE, edit(client))
    assert run["state"] == "succeeded", run
    data, cols = values(client, "independent")
    assert [r[cols["IndependentQty"]] for r in data["rows"]] == ["17.0", "23.0"]

    base_before, base_cols = values(client, "base")
    run = await_run(
        client, BASE, edit(client, mid="IndependentQty", value=100, rows=[], members=[])
    )
    assert run["state"] == "succeeded", run
    data, cols = values(client, "independent")
    assert [r[cols["IndependentQty"]] for r in data["rows"]] == ["50.0", "50.0"]
    data, cols = values(client, "independent_group")
    assert [r[cols["IndependentQty"]] for r in data["rows"]] == ["100.0"]
    data, cols = values(client, "base")
    assert [r[cols["FQTY"]] for r in data["rows"]] == [
        r[base_cols["FQTY"]] for r in base_before["rows"]
    ]
    assert "IndependentQty" not in cols


@pytest.mark.parametrize(
    "mode,sparse,expected",
    [
        ("equal", False, ["100.0", "100.0", "20.0"]),
        ("weighted_by_self", False, ["80.0", "120.0", "20.0"]),
        ("weighted_by_self", True, ["200.0", None, "20.0"]),
    ],
)
def test_edit_native_time_measure_without_base_time_uses_native_and_common_coarser(
    clickhouse_app, mode, sparse, expected
):
    from .test_planning_clickhouse import managed, create, build
    from .test_measures_clickhouse import transaction, measure, assert_post

    client, _, _, _ = clickhouse_app
    seed_view(client)
    calendar = managed(client, "NativeDates", b"day\n2026-01-01\n2026-01-02\n")
    assert_post(
        client,
        "/dimension-members",
        {
            "objectId": "Time",
            "kind": "dimensionMembers",
            "name": "Time",
            "displayName": "Time",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar,
                "columns": [
                    {
                        "columnId": "day",
                        "name": "day",
                        "displayName": "Day",
                        "sourceColumnId": "col_1",
                    }
                ],
                "keyColumnIds": ["day"],
                "labelColumnId": "day",
                "attributeColumnIds": [],
                "time": {
                    "baseGrain": "day",
                    "dateColumnId": "day",
                    "orderingColumnId": "day",
                },
            },
        },
    )
    source = managed(
        client,
        "NativeProductTime",
        b"product,day,quantity\nP1,2026-01-01,40\nP1,2026-01-02,60\nP2,2026-01-01,20\n",
    )
    create(
        client,
        {
            "objectId": "native_time",
            "kind": "dimension",
            "name": "Product Time",
            "displayName": "Product Time",
            "definition": {
                "population": {"operation": "source", "sourceId": source},
                "grain": {
                    "keyIds": ["product", "day"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "Product",
                            "populationRef": {"columnId": "col_1"},
                        },
                        {
                            "grainColumnId": "day",
                            "name": "Day",
                            "populationRef": {"columnId": "col_2"},
                        },
                    ],
                },
                "memberBindings": [
                    {
                        "memberCatalogId": "Product",
                        "keyMappings": [
                            {"grainColumnId": "product", "memberColumnId": "product"}
                        ],
                        "selectedAttributeColumnIds": [],
                    },
                    {
                        "memberCatalogId": "Time",
                        "keyMappings": [
                            {"grainColumnId": "day", "memberColumnId": "day"}
                        ],
                        "selectedAttributeColumnIds": [],
                    },
                ],
            },
        },
    )
    fact_source = (
        managed(
            client,
            "SparseNativeTime",
            b"product,day,quantity\nP1,2026-01-01,40\nP2,2026-01-01,20\n",
        )
        if sparse
        else source
    )
    transaction(
        client,
        "native_time_tx",
        fact_source,
        "native_time",
        [("product", "col_1"), ("day", "col_2")],
    )
    # A stored disaggregation policy does not require a path to Base.
    measure(client, "TimeQty", "native_time_tx", "native_time", "col_3", mode=mode)
    update_measure(client, "TimeQty", lambda d: d.update(editable=True))
    for name, calculation in [
        ("TwiceAF", "AggregateFirst"),
        ("TwiceCF", "CalculateFirst"),
    ]:
        assert_post(
            client,
            "/measures",
            {
                "objectId": name,
                "kind": "measure",
                "name": name,
                "displayName": name,
                "business": {},
                "definition": {
                    "measureType": "calculated",
                    "nativeDimensionId": "native_time",
                    "calculationType": calculation,
                    "resultType": {"type": "number", "nullable": True},
                    "aggregation": {
                        "nonTime": "sum",
                        "time": "sum",
                        "nullInputPolicy": "ignore",
                        "allNullResult": "null",
                    }
                    if calculation == "CalculateFirst"
                    else None,
                    "formula": {
                        "displayText": "TimeQty * 2",
                        "expression": {
                            "type": "binary",
                            "operator": "multiply",
                            "left": {"type": "measureRef", "measureId": "TimeQty"},
                            "right": {"type": "literal", "value": 2},
                        },
                    },
                },
            },
        )
    assert build(client, "native_time")["state"] == "succeeded"
    # Weighted by self is dormant on initial native load; no previous Base weights needed.
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "base")
    assert "TimeQty" not in cols
    data, cols = values(client, "pt")
    assert [r[cols["TimeQty"]] for r in data["rows"]] == [
        "40.0" if sparse else "100.0",
        "20.0",
        None,
    ]
    query = query_view(client, rows=[field("Product", "product")], measures=["TimeQty"])
    assert query.status_code == 200, query.text
    assert query.json()["measures"][0]["writeLevelId"] == "native_time"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="TimeQty",
            value=200,
            rows=[field("Product", "product")],
            members=["P1"],
        ),
    )
    assert run["state"] == "succeeded", run
    data, cols = values(client, "native_time")
    assert [r[cols["TimeQty"]] for r in data["rows"]] == expected
    data, cols = values(client, "pt")
    assert [r[cols["TimeQty"]] for r in data["rows"]] == ["200.0", "20.0", None]
    assert [r[cols["TwiceAF"]] for r in data["rows"]] == ["400.0", "40.0", None]
    assert [r[cols["TwiceCF"]] for r in data["rows"]] == ["400.0", "40.0", None]
    data, cols = values(client, "base")
    assert "TimeQty" not in cols


def test_edit_weighted_self_uses_populated_cells_and_preserves_null_cells(
    clickhouse_app,
):
    from .test_planning_clickhouse import managed
    from .test_measures_clickhouse import transaction, measure

    client, _, _, _ = clickhouse_app
    seed_view(client)
    source = managed(
        client, "SparsePromotions", b"product,location,quantity\nP1,L1,10\nP2,L1,30\n"
    )
    transaction(
        client,
        "sparse_tx",
        source,
        "base",
        [("product", "col_1"), ("location", "col_2")],
    )
    measure(client, "PromoQty", "sparse_tx", "base", "col_3", mode="weighted_by_self")
    update_measure(client, "PromoQty", lambda d: d.update(editable=True))
    assert build_values(client)["state"] == "succeeded"
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="PromoQty",
            value=80,
            rows=[field("Product", "brand")],
            members=["A"],
        ),
    )
    assert run["state"] == "succeeded", run
    data, cols = values(client, "base")
    assert [r[cols["PromoQty"]] for r in data["rows"]] == ["20.0", None, "60.0", None]
    previous = query_view(
        client, rows=[field("Product", "brand")], measures=["PromoQty"]
    ).json()["editContext"]
    # Brand B has no populated self weights: fail clearly and preserve the generation.
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="PromoQty",
            value=50,
            rows=[field("Product", "brand")],
            members=["B"],
        ),
    )
    assert run["state"] == "failed", run
    assert run["error"]["code"] == "empty_self_weights"
    assert (
        query_view(
            client, rows=[field("Product", "brand")], measures=["PromoQty"]
        ).json()["editContext"]
        == previous
    )
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="PromoQty",
            value=0,
            rows=[field("Product", "brand")],
            members=["A"],
        ),
    )
    assert run["state"] == "succeeded", run
    previous = query_view(
        client, rows=[field("Product", "brand")], measures=["PromoQty"]
    ).json()["editContext"]
    run = await_run(
        client,
        BASE,
        edit(
            client,
            mid="PromoQty",
            value=50,
            rows=[field("Product", "brand")],
            members=["A"],
        ),
    )
    assert run["state"] == "failed", run
    assert run["error"]["code"] == "zero_self_weight_total"
    assert (
        query_view(
            client, rows=[field("Product", "brand")], measures=["PromoQty"]
        ).json()["editContext"]
        == previous
    )


def test_edit_persists_six_places_and_rolls_up_same_values(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    editable(client)
    request = edit(
        client,
        value=1.23456789,
        members=("P1", "L1"),
        rows=[field("Product", "product"), field("Location", "location")],
    )
    run = await_run(client, BASE, request)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert finer["rows"][0][cols["FQTY"]] == "1.234568"
    native, cols = values(client, "pt")
    assert native["rows"][0][cols["FQTY"]] == "31.234568"
