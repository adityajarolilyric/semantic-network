# ruff: noqa: F811
from uuid import uuid4
import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_managed_clickhouse import await_run

pytestmark = pytest.mark.integration


AGG = {
    "nonTime": "sum",
    "time": "sum",
    "nullInputPolicy": "ignore",
    "allNullResult": "null",
}


def assert_post(client, path, payload):
    response = client.post(BASE + path, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def transaction(client, oid, source, level, keys):
    return assert_post(
        client,
        "/transactions",
        {
            "objectId": oid,
            "kind": "transaction",
            "name": oid,
            "displayName": oid,
            "definition": {
                "sourceId": source,
                "planningLevelId": level,
                "keyMappings": [
                    {"sourceColumnId": col, "grainColumnId": key} for key, col in keys
                ],
            },
        },
    )


def measure(client, oid, tx, native, value, *, agg=None, mode="none", basis=None):
    return assert_post(
        client,
        "/measures",
        {
            "objectId": oid,
            "kind": "measure",
            "name": oid,
            "displayName": oid,
            "business": {},
            "definition": {
                "measureType": "referenced",
                "transactionId": tx,
                "valueColumnId": value,
                "nativePlanningLevelId": native,
                "resultType": {
                    "type": "decimal",
                    "precision": 38,
                    "scale": 3,
                    "nullable": True,
                },
                "aggregation": agg or AGG,
                "allocation": {
                    "mode": mode,
                    **({"basisMeasureId": basis} if basis else {}),
                },
            },
        },
    )


def setup(
    client,
    qty=b"P1,L1,40.0\nP1,L2,60.0\n",
    *,
    sales=b"P1,120.0\n",
    mode="none",
    agg=None,
):
    pop = managed(client, "Population", b"product,location\nP1,L1\nP1,L2\n")
    level = {
        "objectId": "base",
        "kind": "planningLevel",
        "name": "Base",
        "displayName": "Base",
        "definition": {
            "population": {"operation": "source", "sourceId": pop},
            "grain": {
                "keyIds": ["product", "location"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "product",
                        "populationRef": {"columnId": "col_1"},
                    },
                    {
                        "grainColumnId": "location",
                        "name": "location",
                        "populationRef": {"columnId": "col_2"},
                    },
                ],
            },
        },
    }
    create(client, level)
    create(
        client,
        {
            "objectId": "pt",
            "kind": "planningLevel",
            "name": "Product",
            "displayName": "Product",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourcePlanningLevelId": "base",
                },
                "grain": {
                    "keyIds": ["product"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": "product"},
                        }
                    ],
                },
            },
        },
    )
    rev = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"basePlanningLevelId": "base", "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert response.status_code == 200, response.text
    forecast = managed(client, "ForecastValues", b"product,location,quantity\n" + qty)
    sales_source = managed(client, "SalesValues", b"product,quantity\n" + sales)
    transaction(
        client,
        "tx_forecast",
        forecast,
        "base",
        [("product", "col_1"), ("location", "col_2")],
    )
    transaction(client, "tx_sales", sales_source, "pt", [("product", "col_1")])
    measure(client, "FQTY", "tx_forecast", "base", "col_3")
    measure(
        client,
        "SQTY",
        "tx_sales",
        "pt",
        "col_2",
        mode=mode,
        basis="FQTY" if mode == "weighted" else None,
        agg=agg,
    )
    assert build(client, "base")["state"] == "succeeded"
    assert build(client, "pt")["state"] == "succeeded"


def build_values(client):
    rev = client.get(BASE).json()
    return await_run(
        client,
        BASE,
        client.post(
            BASE + "/measures/execution/build",
            headers={"If-Match": str(rev["version"]), "Idempotency-Key": uuid4().hex},
        ),
    )


def values(client, level):
    response = client.get(
        BASE + "/measures/execution/levels/" + level + "?preview=true"
    )
    assert response.status_code == 200, response.text
    result = response.json()
    return result, {c["name"]: i for i, c in enumerate(result["columns"])}


def test_native_independent_aggregation_repeat_and_formula_recalculation(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    setup(
        client,
        qty=b"P1,L1,10.0\nP1,L1,30.0\nP1,L2,60.0\n",
        agg={**AGG, "nonTime": "avg"},
    )
    calc = {
        "objectId": "Error",
        "kind": "measure",
        "name": "Error",
        "displayName": "Error",
        "business": {},
        "definition": {
            "measureType": "calculated",
            "nativePlanningLevelId": "pt",
            "calculationType": "AggregateFirst",
            "formula": {
                "displayText": "ABS(FQTY-SQTY)",
                "expression": {
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
                },
            },
            "resultType": {
                "type": "decimal",
                "precision": 38,
                "scale": 1,
                "nullable": True,
            },
        },
    }
    assert_post(client, "/measures", calc)
    run = build_values(client)
    assert run["state"] == "succeeded", run
    base, cols = values(client, "base")
    assert base["state"] == "ready" and base["rowCount"] == 2
    assert [r[cols["FQTY"]] for r in base["rows"]] == ["40.0", "60.0"]
    assert [r[cols["SQTY"]] for r in base["rows"]] == ["120.0", "120.0"]
    assert [r[cols["Error"]] for r in base["rows"]] == ["80.0", "60.0"]
    coarse, cols = values(client, "pt")
    assert coarse["rows"][0][cols["FQTY"]] == "100.0"
    assert coarse["rows"][0][cols["SQTY"]] == "120.0"
    assert coarse["rows"][0][cols["Error"]] == "20.0"
    assert coarse["generationId"] == base["generationId"]


@pytest.mark.parametrize(
    "mode,expected",
    [
        (mode, ["60.0", "60.0"])
        for mode in ("none", "equal", "weighted", "weighted_by_self")
    ],
)
def test_materialisation_ignores_edit_disaggregation_policy(
    clickhouse_app, mode, expected
):
    client, _, _, _ = clickhouse_app
    setup(client, mode=mode)
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "base")
    assert [r[cols["SQTY"]] for r in data["rows"]] == expected


def update_measure(client, oid, change):
    original = client.get(BASE + "/measures/" + oid).json()
    payload = {
        k: v
        for k, v in original.items()
        if k not in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
    }
    change(payload["definition"])
    result = client.put(
        BASE + "/measures/" + oid,
        json=payload,
        headers={"If-Match": str(original["version"])},
    )
    assert result.status_code == 200, result.text


def test_initialisation_reconstructs_sum_and_average_on_other_levels(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client, agg={**AGG, "nonTime": "avg"})
    # A separate, equivalent defined grain gets its representation from Base.
    create(
        client,
        {
            "objectId": "pt_report",
            "kind": "planningLevel",
            "name": "Report",
            "displayName": "Report",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourcePlanningLevelId": "base",
                },
                "grain": {
                    "keyIds": ["report_product"],
                    "columns": [
                        {
                            "grainColumnId": "report_product",
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": "product"},
                        }
                    ],
                },
            },
        },
    )
    assert build(client, "pt_report")["state"] == "succeeded"
    result = build_values(client)
    assert result["state"] == "succeeded", result
    result, cols = values(client, "pt_report")
    assert result["rows"][0][cols["SQTY"]] == "120.0"
    update_measure(client, "SQTY", lambda d: d["aggregation"].update(nonTime="sum"))
    result = build_values(client)
    assert result["state"] == "succeeded", result
    result, cols = values(client, "pt_report")
    assert result["rows"][0][cols["SQTY"]] == "120.0"
    native, cols = values(client, "pt")
    assert native["rows"][0][cols["SQTY"]] == "120.0"


def test_failed_generation_keeps_all_previous_bindings(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client)
    assert build_values(client)["state"] == "succeeded"
    before_base, base_cols = values(client, "base")
    before_pt, _ = values(client, "pt")
    zero = {
        "objectId": "ZeroWeight",
        "kind": "measure",
        "name": "ZeroWeight",
        "displayName": "ZeroWeight",
        "business": {},
        "definition": {
            "measureType": "calculated",
            "nativePlanningLevelId": "base",
            "calculationType": "CalculateFirst",
            "aggregation": AGG,
            "formula": {
                "displayText": "FQTY*0",
                "expression": {
                    "type": "binary",
                    "operator": "multiply",
                    "left": {"type": "measureRef", "measureId": "FQTY"},
                    "right": {"type": "literal", "value": 0},
                },
            },
            "resultType": {
                "type": "decimal",
                "precision": 38,
                "scale": 1,
                "nullable": True,
            },
        },
    }
    assert_post(client, "/measures", zero)
    update_measure(
        client,
        "SQTY",
        lambda d: d.update(
            allocation={"mode": "weighted", "basisMeasureId": "ZeroWeight"}
        ),
    )
    # A genuine execution limit must still preserve every previous binding.
    original_limit = (
        client.app.state.container.measure_builds.settings.measure_max_sql_bytes
    )
    client.app.state.container.measure_builds.settings.measure_max_sql_bytes = 1
    run = build_values(client)
    client.app.state.container.measure_builds.settings.measure_max_sql_bytes = (
        original_limit
    )
    assert run["state"] == "failed" and run["error"]["code"] == "measure_sql_limit", run
    after_base, _ = values(client, "base")
    after_pt, _ = values(client, "pt")
    assert (
        after_base["generationId"]
        == before_base["generationId"]
        == after_pt["generationId"]
        == before_pt["generationId"]
    )
    assert after_base["rows"] == before_base["rows"]


def test_source_rebuild_ignores_previous_self_weights(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    setup(client, mode="weighted")
    assert build_values(client)["state"] == "succeeded"
    # Simulate an older proportional generation. Reinitialisation must not use
    # its distribution, even if the edit policy is Weighted by self.
    repo = client.app.state.container.store.repository
    binding = client.portal.call(
        repo.get,
        "engine_artifact_bindings",
        {"resourceKind": "measureValues", "resourceId": "base"},
    )
    previous = client.portal.call(
        repo.get, "engine_artifacts", {"artifactId": binding["artifactId"]}
    )
    column = previous["measureColumns"]["SQTY"]
    location = previous["keyColumns"]["location"]
    admin.command(
        f"ALTER TABLE `{database}`.`{previous['physicalName']}` UPDATE `{column}`=if(`{location}`='L1',48,72) WHERE 1 SETTINGS mutations_sync=1"
    )
    old, old_cols = values(client, "base")
    assert [row[old_cols["SQTY"]] for row in old["rows"]] == ["48.0", "72.0"]
    update_measure(
        client, "SQTY", lambda d: d.update(allocation={"mode": "weighted_by_self"})
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "base")
    assert [r[cols["SQTY"]] for r in data["rows"]] == ["60.0", "60.0"]


@pytest.mark.parametrize(
    "non_time,time_method,order,expected",
    [
        ("sum", "last", "nonTimeThenTime", "100.0"),
        ("sum", "last", "timeThenNonTime", "130.0"),
        ("avg", "sum", "nonTimeThenTime", "120.0"),
        ("avg", "sum", "timeThenNonTime", "70.0"),
    ],
)
def test_time_and_non_time_policies_and_order(
    clickhouse_app, non_time, time_method, order, expected
):
    client, _, _, _ = clickhouse_app
    pop = managed(
        client,
        "Population",
        b"product,location,date\nP1,L1,2026-01-01\nP1,L2,2026-01-01\nP1,L1,2026-01-02\n",
    )
    calendar = managed(
        client, "Calendar", b"date,month\n2026-01-01,January\n2026-01-02,January\n"
    )
    dim = {
        "objectId": "dim_time",
        "kind": "dimension",
        "name": "Time",
        "displayName": "Time",
        "isTimeDim": True,
        "definition": {
            "sourceId": calendar,
            "columns": [
                {
                    "columnId": "date",
                    "name": "date",
                    "displayName": "Date",
                    "sourceColumnId": "col_1",
                },
                {
                    "columnId": "month",
                    "name": "month",
                    "displayName": "Month",
                    "sourceColumnId": "col_2",
                },
            ],
            "keyColumnIds": ["date"],
            "labelColumnId": "date",
            "attributeColumnIds": ["date", "month"],
            "time": {
                "baseGrain": "day",
                "dateColumnId": "date",
                "orderingColumnId": "date",
            },
        },
    }
    assert_post(client, "/dimensions", dim)
    base = {
        "objectId": "base",
        "kind": "planningLevel",
        "name": "Base",
        "displayName": "Base",
        "definition": {
            "population": {"operation": "source", "sourceId": pop},
            "grain": {
                "keyIds": ["product", "location", "date"],
                "columns": [
                    {
                        "grainColumnId": key,
                        "name": key,
                        "populationRef": {"columnId": f"col_{i}"},
                    }
                    for i, key in enumerate(["product", "location", "date"], 1)
                ],
            },
            "dimensionBindings": [
                {
                    "dimensionId": "dim_time",
                    "keyMappings": [
                        {"grainColumnId": "date", "dimensionColumnId": "date"}
                    ],
                    "selectedAttributeColumnIds": [],
                }
            ],
        },
    }
    create(client, base)
    create(
        client,
        {
            "objectId": "product",
            "kind": "planningLevel",
            "name": "Product",
            "displayName": "Product",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourcePlanningLevelId": "base",
                },
                "grain": {
                    "keyIds": ["product"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": "product"},
                        }
                    ],
                },
            },
        },
    )
    rev = client.get(BASE).json()
    assert (
        client.put(
            BASE,
            json={"basePlanningLevelId": "base", "changeReason": ""},
            headers={"If-Match": str(rev["version"])},
        ).status_code
        == 200
    )
    tx = managed(
        client,
        "Quantity",
        b"product,location,date,value\nP1,L1,2026-01-01,10.0\nP1,L2,2026-01-01,30.0\nP1,L1,2026-01-02,100.0\n",
    )
    transaction(
        client,
        "tx",
        tx,
        "base",
        [("product", "col_1"), ("location", "col_2"), ("date", "col_3")],
    )
    measure(
        client,
        "QTY",
        "tx",
        "base",
        "col_4",
        agg={**AGG, "nonTime": non_time, "time": time_method, "order": order},
    )
    assert build(client, "base")["state"] == "succeeded"
    assert build(client, "product")["state"] == "succeeded"
    run = build_values(client)
    assert run["state"] == "succeeded", run
    data, cols = values(client, "product")
    assert data["rows"][0][cols["QTY"]] == expected


@pytest.mark.parametrize("exposed_projection", [False, True])
def test_dimension_attribute_hierarchy_and_parent_attribute_keys(
    clickhouse_app, exposed_projection
):
    from backend.app.engine.contracts import digest

    client, _, _, _ = clickhouse_app
    setup(client)
    catalog = managed(client, "Products", b"product,brand\nP1,BrandA\n")
    assert_post(
        client,
        "/dimensions",
        {
            "objectId": "products_dim",
            "kind": "dimension",
            "name": "Products",
            "displayName": "Products",
            "isTimeDim": False,
            "definition": {
                "sourceId": catalog,
                "columns": [
                    {
                        "columnId": name,
                        "name": name,
                        "displayName": name,
                        "sourceColumnId": f"col_{index}",
                    }
                    for index, name in enumerate(["product", "brand"], 1)
                ],
                "keyColumnIds": ["product"],
                "labelColumnId": "product",
                "attributeColumnIds": ["product", "brand"],
            },
        },
    )
    original = client.get(BASE + "/planning-levels/base").json()
    payload = {
        k: v
        for k, v in original.items()
        if k not in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
    }
    payload["definition"]["dimensionBindings"] = [
        {
            "dimensionId": "products_dim",
            "keyMappings": [
                {"grainColumnId": "product", "dimensionColumnId": "product"}
            ],
            "selectedAttributeColumnIds": ["brand"] if exposed_projection else [],
        }
    ]
    result = client.put(
        BASE + "/planning-levels/base",
        json=payload,
        headers={"If-Match": str(original["version"])},
    )
    assert result.status_code == 200, result.text
    assert build(client, "base", result.json()["version"])["state"] == "succeeded"
    assert build(client, "pt")["state"] == "succeeded"
    derived_id = "da_" + digest(["base", "products_dim", "brand"])[:28]
    create(
        client,
        {
            "objectId": "brands",
            "kind": "planningLevel",
            "name": "Brands",
            "displayName": "Brands",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourcePlanningLevelId": "base",
                }
                if exposed_projection
                else {"operation": "source", "sourceId": catalog},
                "grain": {
                    "keyIds": ["brand"],
                    "columns": [
                        {
                            "grainColumnId": "brand",
                            "name": "brand",
                            "populationRef": {"sourceOutputColumnId": derived_id}
                            if exposed_projection
                            else {"columnId": "col_2"},
                        }
                    ],
                },
            },
        },
    )
    assert build(client, "brands")["state"] == "succeeded"
    paths = client.get(BASE + "/measures/FQTY/capabilities").json()
    path = next(p for p in paths["levels"] if p["planningLevelId"] == "brands")
    assert path["relationship"] == "roll_up" and path["readable"]
    result = build_values(client)
    assert result["state"] == "succeeded", result
    data, cols = values(client, "brands")
    assert data["rows"][0][cols["FQTY"]] == "100.0"
    assert data["rows"][0][cols["SQTY"]] == "120.0"


def test_initial_self_edit_policy_does_not_require_previous_values(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client, mode="weighted_by_self")
    result = build_values(client)
    assert result["state"] == "succeeded", result
    base, cols = values(client, "base")
    assert [r[cols["SQTY"]] for r in base["rows"]] == ["60.0", "60.0"]
    assert not base["incompleteMeasures"]
    native, nc = values(client, "pt")
    assert native["rows"][0][nc["SQTY"]] == "120.0"
    assert not native["incompleteMeasures"]


@pytest.mark.parametrize(
    "method,expected",
    [
        ("sum", "100.0"),
        ("avg", "50.0"),
        ("min", "40.0"),
        ("max", "60.0"),
        ("count", "2.0"),
        ("count_distinct", "1.0"),
    ],
)
def test_every_non_time_aggregate_comes_from_measure_configuration(
    clickhouse_app, method, expected
):
    client, _, _, _ = clickhouse_app
    setup(client)
    update_measure(client, "FQTY", lambda d: d["aggregation"].update(nonTime=method))
    result = build_values(client)
    assert result["state"] == "succeeded", result
    data, cols = values(client, "pt")
    # COUNT at native grain first counts source rows, then counts Base values.
    assert data["rows"][0][cols["FQTY"]] == expected


def test_base_native_self_policy_is_dormant_during_initial_load(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client)
    update_measure(
        client, "FQTY", lambda d: d.update(allocation={"mode": "weighted_by_self"})
    )
    result = build_values(client)
    assert result["state"] == "succeeded", result
    data, cols = values(client, "base")
    assert [r[cols["FQTY"]] for r in data["rows"]] == ["40.0", "60.0"]


def test_decimal_precision_overflow_does_not_publish_candidates(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client)
    assert_post(
        client,
        "/measures",
        {
            "objectId": "TooLarge",
            "kind": "measure",
            "name": "TooLarge",
            "displayName": "TooLarge",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativePlanningLevelId": "base",
                "calculationType": "CalculateFirst",
                "aggregation": AGG,
                "formula": {
                    "displayText": "1000",
                    "expression": {"type": "literal", "value": 1000},
                },
                "resultType": {
                    "type": "decimal",
                    "precision": 5,
                    "scale": 2,
                    "nullable": True,
                },
            },
        },
    )
    result = build_values(client)
    assert (
        result["state"] == "failed"
        and result["error"]["code"] == "measure_numeric_overflow"
    ), result
    data, _ = values(client, "base")
    assert data["generationId"] is None


@pytest.mark.parametrize(
    "mode,expected", [("equal", [0.5, 0.5]), ("weighted", [0.5, 0.5])]
)
def test_integer_sources_disaggregate_to_fractional_float64(
    clickhouse_app, mode, expected
):
    client, _, _, _ = clickhouse_app
    setup(client, qty=b"P1,L1,40\nP1,L2,60\n", sales=b"P1,1\n", mode=mode)
    result = build_values(client)
    assert result["state"] == "succeeded", result
    data, cols = values(client, "base")
    quantity = next(c for c in data["columns"] if c["name"] == "SQTY")
    assert quantity["physicalType"] == "Float64"
    assert [float(row[cols["SQTY"]]) for row in data["rows"]] == pytest.approx(expected)
    assert (
        client.get(BASE + "/measures/SQTY").json()["definition"]["resultType"]["type"]
        == "number"
    )


def test_legacy_integer_reference_builds_as_float64_without_resaving(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client, qty=b"P1,L1,40\nP1,L2,60\n", sales=b"P1,1\n", mode="equal")
    repo = client.app.state.container.store.repository
    query = {"modelId": "default_model", "revisionId": "revision_1", "objectId": "SQTY"}
    legacy = client.portal.call(repo.get, "semantic_objects", query)
    legacy["definition"]["resultType"] = {"type": "integer", "nullable": True}
    client.portal.call(repo.replace, "semantic_objects", query, legacy)
    assert (
        client.get(BASE + "/measures/SQTY").json()["definition"]["resultType"]["type"]
        == "number"
    )
    result = build_values(client)
    assert result["state"] == "succeeded", result
    data, cols = values(client, "base")
    assert data["state"] == "ready"
    assert [float(row[cols["SQTY"]]) for row in data["rows"]] == [0.5, 0.5]
    assert (
        client.portal.call(repo.get, "semantic_objects", query)["definition"][
            "resultType"
        ]["type"]
        == "integer"
    )


def test_rebuilds_reuse_exactly_two_tables_per_level_and_isolate_candidates(
    clickhouse_app, monkeypatch
):
    """Observe physical tables during every INSERT, including failed attempts."""
    from backend.app.core.errors import MetadataError

    client, admin, database, _ = clickhouse_app
    setup(client)
    assert build_values(client)["state"] == "succeeded"
    container = client.app.state.container
    repo = container.store.repository

    async def list_resources():
        return await repo.list(
            "engine_table_resources", {}, key="physicalName", limit=100
        )

    def names():
        return {r["physicalName"] for r in client.portal.call(list_resources)}

    permanent = names()
    assert len(permanent) == 4
    for resource in client.portal.call(list_resources):
        label = "Base" if resource["resourceId"] == "base" else "Product"
        mat = "_mat" if resource["resourceKind"] == "measureValues" else ""
        assert resource["physicalName"].startswith(f"grain_{label}{mat}_")

    def assert_physical_tables():
        actual = {
            row[0]
            for row in admin.query(
                "SELECT name FROM system.tables WHERE database={db:String} AND engine='MergeTree' AND (startsWith(comment,'semanticTable:') OR startsWith(comment,'semanticArtifact:'))",
                parameters={"db": database},
            ).result_rows
        }
        assert actual == permanent

    assert_physical_tables()
    catalog = client.get("/api/v1/warehouse/tables")
    assert catalog.status_code == 200, catalog.text
    assert not permanent.intersection(t["name"] for t in catalog.json()["tables"])
    for name in permanent:
        assert client.get(f"/api/v1/warehouse/tables/{name}/preview").status_code == 404
    before, _ = values(client, "base")
    original = container.measure_builds.executor.build
    observed = []

    async def observe(artifact, compiled, plan):
        assert_physical_tables()
        outcome = await original(artifact, compiled, plan)
        assert_physical_tables()
        # Even after the INSERT, independent readers see the old active generation.
        visible = await container.measure_builds.status(
            "default_model", "revision_1", "base", preview=True
        )
        observed.append(visible["generationId"])
        assert visible["rows"] == before["rows"]
        return outcome

    monkeypatch.setattr(container.measure_builds.executor, "build", observe)
    measure(client, "Extra", "tx_forecast", "base", "col_3")
    assert build_values(client)["state"] == "succeeded"
    assert observed == [before["generationId"], before["generationId"]]
    assert names() == permanent
    after, cols = values(client, "base")
    assert "Extra" in cols and len(after["rows"]) == 2

    # Removing a measure changes the active logical schema, reusing the same table.
    monkeypatch.setattr(container.measure_builds.executor, "build", original)
    extra = client.get(BASE + "/measures/Extra").json()
    response = client.delete(
        BASE + "/measures/Extra", headers={"If-Match": str(extra["version"])}
    )
    assert response.status_code == 204, response.text
    assert build_values(client)["state"] == "succeeded"
    before_failure, cols = values(client, "base")
    assert "Extra" not in cols

    # Fail after both levels have written candidate rows. Neither may become active.
    async def fail_second(artifact, compiled, plan):
        result = await original(artifact, compiled, plan)
        assert_physical_tables()
        if artifact["resourceId"] == "pt":
            raise MetadataError(
                "test_failure", "Injected failure after writing the second level."
            )
        return result

    monkeypatch.setattr(container.measure_builds.executor, "build", fail_second)
    assert build_values(client)["state"] == "failed"
    visible, _ = values(client, "base")
    assert visible["generationId"] == before_failure["generationId"]
    assert visible["rows"] == before_failure["rows"]
    assert names() == permanent
    assert_physical_tables()

    # Population rebuilds also reuse their physical tables; derived reads filter rows.
    assert build(client, "base")["state"] == "succeeded"
    assert build(client, "pt")["state"] == "succeeded"
    assert names() == permanent
    assert_physical_tables()

    # Status route is checked below through the service to keep this test about storage.
    async def preview_population():
        return await container.planning_builds.status(
            "default_model", "revision_1", "pt", preview=True
        )

    assert len(client.portal.call(preview_population)["rows"]) == 1


def test_generation_collection_preserves_shared_tables_and_active_rows(clickhouse_app):
    from datetime import timedelta
    from backend.app.engine.artifacts import ArtifactCollector
    from backend.app.services.base import now

    client, admin, database, _ = clickhouse_app
    setup(client)
    assert build_values(client)["state"] == "succeeded"
    assert build_values(client)["state"] == "succeeded"
    container = client.app.state.container
    before, _ = values(client, "base")

    async def collect():
        repo = container.store.repository
        artifacts = await repo.list("engine_artifacts", {}, key="artifactId", limit=100)
        retired = []
        for artifact in artifacts:
            if artifact.get("storageMode") == "versioned":
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": artifact["artifactId"]},
                    {**artifact, "createdAt": now() - timedelta(days=2)},
                )
                if not await repo.get(
                    "engine_artifact_bindings", {"artifactId": artifact["artifactId"]}
                ):
                    retired.append(artifact)
        await ArtifactCollector(container.managed_tables).collect()
        return retired

    retired = client.portal.call(collect)
    assert len(retired) == 2
    for artifact in retired:
        assert (
            admin.command(f"EXISTS TABLE `{database}`.`{artifact['physicalName']}`")
            == 1
        )
        assert (
            int(
                admin.command(
                    f"SELECT count() FROM `{database}`.`{artifact['physicalName']}` WHERE __semantic_generation={{gen:String}}",
                    parameters={"gen": artifact["generationToken"]},
                )
            )
            == 0
        )
    after, _ = values(client, "base")
    assert after["rows"] == before["rows"]
    assert after["generationId"] == before["generationId"]


def test_legacy_tables_are_adopted_and_obsolete_tables_removed(clickhouse_app):
    """Emulate the prior layout, with no generation column or resource registry."""
    from copy import deepcopy
    from backend.app.warehouse.repository import (
        quote_identifier as q,
        quote_literal as lit,
    )
    from backend.app.services.base import now

    client, admin, database, _ = clickhouse_app
    setup(client)
    assert build_values(client)["state"] == "succeeded"
    container = client.app.state.container
    repo = container.store.repository

    async def legacy_documents():
        resources = await repo.list(
            "engine_table_resources", {}, key="physicalName", limit=100
        )
        artifacts = []
        for resource in resources:
            binding = await repo.get(
                "engine_artifact_bindings",
                container.planning_builds.storage.identity(resource),
            )
            artifact = await repo.get(
                "engine_artifacts", {"artifactId": binding["artifactId"]}
            )
            for field in ["storageMode", "storageOwner", "generationToken"]:
                artifact.pop(field, None)
            await repo.replace(
                "engine_artifacts", {"artifactId": artifact["artifactId"]}, artifact
            )
            await repo.delete(
                "engine_table_resources",
                container.planning_builds.storage.identity(resource),
            )
            artifacts.append(artifact)
        return artifacts

    artifacts = client.portal.call(legacy_documents)
    original_names = {a["physicalName"] for a in artifacts}
    for artifact in artifacts:
        table = q(database) + "." + q(artifact["physicalName"])
        columns = artifact["columns"]
        data = admin.query(
            f"SELECT {', '.join(q(c.get('storageName', c['name'])) for c in columns)} FROM {table}"
        ).result_rows
        admin.command(f"DROP TABLE {table} SYNC")
        declarations = ", ".join(
            q(c.get("storageName", c["name"]))
            + " "
            + (f"Nullable({c['physicalType']})" if c["nullable"] else c["physicalType"])
            for c in columns
        )
        admin.command(
            f"CREATE TABLE {table} ({declarations}) ENGINE=MergeTree ORDER BY tuple() COMMENT {lit('semanticArtifact:' + artifact['artifactId'])}"
        )
        admin.insert(
            artifact["physicalName"],
            data,
            column_names=[c.get("storageName", c["name"]) for c in columns],
            database=database,
        )

    # One unbound table from an older terminal build should be reclaimed on upgrade.
    old = deepcopy(next(a for a in artifacts if a["resourceKind"] == "measureValues"))
    old.update(
        artifactId="artifact_legacyold",
        physicalName="__semantic_values_legacyold",
        createdAt=now(),
    )
    admin.command(
        f"CREATE TABLE `{database}`.`{old['physicalName']}` (x UInt8) ENGINE=MergeTree ORDER BY tuple() COMMENT {lit('semanticArtifact:' + old['artifactId'])}"
    )
    client.portal.call(repo.insert, "engine_artifacts", old)

    assert build(client, "base")["state"] == "succeeded"
    assert build(client, "pt")["state"] == "succeeded"
    assert build_values(client)["state"] == "succeeded"

    async def resource_names():
        return {
            r["physicalName"]
            for r in await repo.list(
                "engine_table_resources", {}, key="physicalName", limit=100
            )
        }

    assert client.portal.call(resource_names) == original_names
    assert admin.command(f"EXISTS TABLE `{database}`.`{old['physicalName']}`") == 0
    data, cols = values(client, "base")
    assert [r[cols["FQTY"]] for r in data["rows"]] == ["40.0", "60.0"]


def test_removed_measure_columns_retire_and_readdition_uses_new_storage_slot(
    clickhouse_app,
):
    from datetime import timedelta
    from backend.app.engine.artifacts import ArtifactCollector
    from backend.app.services.base import now

    client, admin, database, _ = clickhouse_app
    setup(client)
    measure(client, "Extra", "tx_forecast", "base", "col_3")
    assert build_values(client)["state"] == "succeeded"
    before, _ = values(client, "base")
    old_name = next(
        c["storageName"] for c in before["columns"] if c.get("measureId") == "Extra"
    )
    extra = client.get(BASE + "/measures/Extra").json()
    assert (
        client.delete(
            BASE + "/measures/Extra", headers={"If-Match": str(extra["version"])}
        ).status_code
        == 204
    )
    assert build_values(client)["state"] == "succeeded"
    container = client.app.state.container

    async def collect():
        repo = container.store.repository
        for artifact in await repo.list(
            "engine_artifacts", {}, key="artifactId", limit=100
        ):
            if artifact.get("storageMode") == "versioned":
                await repo.replace(
                    "engine_artifacts",
                    {"artifactId": artifact["artifactId"]},
                    {**artifact, "createdAt": now() - timedelta(days=2)},
                )
        await ArtifactCollector(container.managed_tables).collect()
        return await repo.get(
            "engine_table_resources",
            {
                "modelId": "default_model",
                "revisionId": "revision_1",
                "resourceKind": "measureValues",
                "resourceId": "base",
            },
        )

    resource = client.portal.call(collect)
    assert old_name in resource["retiredNames"]
    assert (
        int(
            admin.command(
                "SELECT count() FROM system.columns WHERE database={db:String} AND table={table:String} AND name={name:String}",
                parameters={
                    "db": database,
                    "table": resource["physicalName"],
                    "name": old_name,
                },
            )
        )
        == 0
    )
    measure(client, "Extra", "tx_forecast", "base", "col_3")
    assert build_values(client)["state"] == "succeeded"
    after, cols = values(client, "base")
    new_name = next(
        c["storageName"] for c in after["columns"] if c.get("measureId") == "Extra"
    )
    assert new_name != old_name
    assert [r[cols["Extra"]] for r in after["rows"]] == ["40.0", "60.0"]


def test_one_time_table_rename_resumes_and_preserves_snapshots(
    clickhouse_app, monkeypatch, tmp_path
):
    from backend.app.engine.contracts import digest
    from backend.app.engine.table_storage import TableStorage
    from backend.scripts.rename_planning_tables import PlanningTableRename

    client, admin, database, _ = clickhouse_app
    naming = TableStorage.physical_name
    # Emulate the previous service's names without adding another physical table.
    monkeypatch.setattr(
        TableStorage,
        "physical_name",
        staticmethod(
            lambda label, identity, allocation: "__semantic_old_"
            + digest([identity, allocation])[:28]
        ),
    )
    setup(client, mode="weighted")
    assert build_values(client)["state"] == "succeeded"
    before, _ = values(client, "base")
    monkeypatch.setattr(TableStorage, "physical_name", staticmethod(naming))
    container = client.app.state.container
    migration = PlanningTableRename(
        container.store,
        container.warehouse_repository,
        container.workspace_gate,
        tmp_path / "rename.json",
        database,
    )
    patch = migration.rewrite_metadata

    async def injected_failure(mapping):
        raise RuntimeError(
            "Interrupted after ClickHouse renames, before reference updates"
        )

    monkeypatch.setattr(migration, "rewrite_metadata", injected_failure)
    with pytest.raises(RuntimeError, match="Interrupted"):
        client.portal.call(migration.apply)
    assert client.get(BASE + "/planning-levels").status_code == 503
    monkeypatch.setattr(migration, "rewrite_metadata", patch)
    journal = client.portal.call(migration.apply)
    assert journal["state"] == "complete"
    assert len(journal["entries"]) == 4
    for entry in journal["entries"]:
        assert entry["new"].startswith("grain_")
        assert admin.command(f"EXISTS TABLE `{database}`.`{entry['old']}`") == 0
        assert admin.command(f"EXISTS TABLE `{database}`.`{entry['new']}`") == 1
    after, _ = values(client, "base")
    assert after["rows"] == before["rows"]
    assert after["generationId"] == before["generationId"]

    async def inspect_snapshots():
        repo = container.store.repository
        runs = await repo.list("engine_runs", {}, key="runId", limit=100)
        for run in runs:
            for snapshot in await repo.list(
                "engine_run_inputs", {"runId": run["runId"]}, key="inputId", limit=100
            ):
                assert "__semantic_old_" not in str(snapshot)

    client.portal.call(inspect_snapshots)
    # Previous frozen Base values still resolve after renaming.
    update_measure(
        client, "SQTY", lambda d: d.update(allocation={"mode": "weighted_by_self"})
    )
    assert build_values(client)["state"] == "succeeded"
    assert values(client, "base")[0]["rows"] == before["rows"]
    assert client.portal.call(migration.apply) == journal


@pytest.mark.parametrize(
    "qty,expected,incomplete",
    [
        (b"P1,L1,40\n", ["60.0", "60.0"], 0),
        (b"P1,L1,0\nP1,L2,0\n", ["60.0", "60.0"], 0),
    ],
)
def test_missing_edit_basis_does_not_block_materialisation(
    clickhouse_app, qty, expected, incomplete
):
    client, _, _, _ = clickhouse_app
    setup(client, qty=qty, mode="weighted")
    run = build_values(client)
    assert run["state"] == "succeeded", run
    base, cols = values(client, "base")
    assert [r[cols["SQTY"]] for r in base["rows"]] == expected
    assert base["incompleteMeasures"].get("SQTY", 0) == incomplete
    assert all(c.get("role") != "coverage" for c in base["columns"])
    assert len(base["rows"][0]) == len(base["columns"])
    native, nc = values(client, "pt")
    assert native["rows"][0][nc["SQTY"]] == "120.0"
    assert not native["incompleteMeasures"]


def test_nonnullable_native_materialises_without_edit_weights(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client, mode="weighted_by_self")
    update_measure(
        client,
        "SQTY",
        lambda d: d.update(
            missingTransactionValue="zero", aggregation={**AGG, "allNullResult": "zero"}
        ),
    )
    assert (
        client.get(BASE + "/measures/SQTY").json()["definition"]["resultType"][
            "nullable"
        ]
        is False
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert [row[cols["SQTY"]] for row in finer["rows"]] == ["60.0", "60.0"]
    assert finer["columns"][cols["SQTY"]]["nullable"] is False
    native, cols = values(client, "pt")
    assert native["rows"][0][cols["SQTY"]] == "120.0"


def test_formula_inputs_materialise_without_edit_basis(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    setup(client, qty=b"P1,L1,0\nP1,L2,0\n", mode="weighted")
    assert_post(
        client,
        "/measures",
        {
            "objectId": "SafeFormula",
            "kind": "measure",
            "name": "SafeFormula",
            "displayName": "SafeFormula",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativePlanningLevelId": "base",
                "calculationType": "AggregateFirst",
                "resultType": {"type": "number", "nullable": True},
                "formula": {
                    "displayText": "COALESCE(SQTY,0)",
                    "expression": {
                        "type": "call",
                        "function": "coalesce",
                        "arguments": [
                            {"type": "measureRef", "measureId": "SQTY"},
                            {"type": "literal", "value": 0},
                        ],
                    },
                },
            },
        },
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert all(row[cols["SafeFormula"]] == "60.0" for row in finer["rows"])
    assert not finer["incompleteMeasures"]
    native, cols = values(client, "pt")
    assert native["rows"][0][cols["SafeFormula"]] == "120.0"


def test_genuine_nonnullable_native_null_still_fails(clickhouse_app):
    client, _, _, _ = clickhouse_app
    setup(client, qty=b"P1,L1,40\n", mode="weighted")
    assert_post(
        client,
        "/measures",
        {
            "objectId": "RequiredResult",
            "kind": "measure",
            "name": "RequiredResult",
            "displayName": "RequiredResult",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativePlanningLevelId": "base",
                "calculationType": "CalculateFirst",
                "aggregation": AGG,
                "resultType": {"type": "number", "nullable": False},
                "formula": {
                    "displayText": "1/0",
                    "expression": {
                        "type": "binary",
                        "operator": "divide",
                        "left": {"type": "literal", "value": 1},
                        "right": {"type": "literal", "value": 0},
                    },
                },
            },
        },
    )
    run = build_values(client)
    assert run["state"] == "failed", run
    assert run["error"]["code"] == "measure_null_result"
    assert values(client, "base")[0]["generationId"] is None


@pytest.mark.parametrize(
    "method,expected", [("sum", 60), ("avg", 120), ("min", 120), ("max", 120)]
)
def test_materialisation_non_time_axis_follows_aggregation(
    clickhouse_app, method, expected
):
    client, _, _, _ = clickhouse_app
    setup(client, mode="equal", agg={**AGG, "nonTime": method})
    run = build_values(client)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert [float(row[cols["SQTY"]]) for row in finer["rows"]] == [expected, expected]
    native, cols = values(client, "pt")
    assert float(native["rows"][0][cols["SQTY"]]) == 120


@pytest.mark.parametrize(
    "non_time,time,order,expected",
    [
        ("sum", "avg", "nonTimeThenTime", [60, 120, 60]),
        ("sum", "avg", "timeThenNonTime", [60, 60, 60]),
        ("avg", "sum", "nonTimeThenTime", [60, 60, 60]),
        ("avg", "sum", "timeThenNonTime", [60, 60, 120]),
        ("sum", "sum", "nonTimeThenTime", [30, 60, 30]),
        ("sum", "sum", "timeThenNonTime", [30, 30, 60]),
        ("max", "min", "nonTimeThenTime", [120, 120, 120]),
    ],
)
def test_materialisation_reverses_axes_on_irregular_intersections(
    clickhouse_app, non_time, time, order, expected
):
    client, _, _, _ = clickhouse_app
    pop = managed(
        client,
        "Intersections",
        b"product,location,date\nP1,L1,2026-01-01\nP1,L1,2026-01-02\nP1,L2,2026-01-01\n",
    )
    calendar = managed(client, "Calendar", b"date\n2026-01-01\n2026-01-02\n")
    assert_post(
        client,
        "/dimensions",
        {
            "objectId": "Time",
            "kind": "dimension",
            "name": "Time",
            "displayName": "Time",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar,
                "columns": [
                    {
                        "columnId": "date",
                        "name": "date",
                        "displayName": "Date",
                        "sourceColumnId": "col_1",
                    }
                ],
                "keyColumnIds": ["date"],
                "attributeColumnIds": ["date"],
                "labelColumnId": "date",
                "time": {
                    "baseGrain": "day",
                    "dateColumnId": "date",
                    "orderingColumnId": "date",
                },
            },
        },
    )
    create(
        client,
        {
            "objectId": "base",
            "kind": "planningLevel",
            "name": "Base",
            "displayName": "Base",
            "definition": {
                "population": {"operation": "source", "sourceId": pop},
                "grain": {
                    "keyIds": ["product", "location", "date"],
                    "columns": [
                        {
                            "grainColumnId": name,
                            "name": name,
                            "populationRef": {"columnId": f"col_{index}"},
                        }
                        for index, name in enumerate(["product", "location", "date"], 1)
                    ],
                },
                "dimensionBindings": [
                    {
                        "dimensionId": "Time",
                        "keyMappings": [
                            {"grainColumnId": "date", "dimensionColumnId": "date"}
                        ],
                        "selectedAttributeColumnIds": [],
                    }
                ],
            },
        },
    )
    create(
        client,
        {
            "objectId": "pt",
            "kind": "planningLevel",
            "name": "Product",
            "displayName": "Product",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourcePlanningLevelId": "base",
                },
                "grain": {
                    "keyIds": ["product"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": "product"},
                        }
                    ],
                },
            },
        },
    )
    rev = client.get(BASE).json()
    assert (
        client.put(
            BASE,
            json={"basePlanningLevelId": "base", "changeReason": ""},
            headers={"If-Match": str(rev["version"])},
        ).status_code
        == 200
    )
    source = managed(client, "Totals", b"product,quantity\nP1,120\n")
    transaction(client, "totals", source, "pt", [("product", "col_1")])
    measure(
        client,
        "QTY",
        "totals",
        "pt",
        "col_2",
        mode="weighted_by_self",
        agg={**AGG, "nonTime": non_time, "time": time, "order": order},
    )
    for level_id in ["base", "pt"]:
        assert build(client, level_id)["state"] == "succeeded"
    run = build_values(client)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert [float(row[cols["QTY"]]) for row in finer["rows"]] == expected
    native, cols = values(client, "pt")
    assert float(native["rows"][0][cols["QTY"]]) == 120


@pytest.mark.parametrize(
    "time_method,expected",
    [("sum", [50, 50, 90]), ("avg", [100, 100, 90]), ("last", [100, 100, 90])],
)
def test_time_only_materialisation_uses_time_policy_and_each_parent_period(
    clickhouse_app, time_method, expected
):
    from backend.app.engine.contracts import digest

    client, _, _, _ = clickhouse_app
    pop = managed(
        client,
        "DailyIntersections",
        b"product,day\nP1,2026-01-01\nP1,2026-01-02\nP1,2026-01-08\n",
    )
    calendar = managed(
        client,
        "DailyCalendar",
        b"day,week\n2026-01-01,W1\n2026-01-02,W1\n2026-01-08,W2\n",
    )
    assert_post(
        client,
        "/dimensions",
        {
            "objectId": "Time",
            "kind": "dimension",
            "name": "Time",
            "displayName": "Time",
            "isTimeDim": True,
            "definition": {
                "sourceId": calendar,
                "columns": [
                    {
                        "columnId": name,
                        "name": name,
                        "displayName": name,
                        "sourceColumnId": f"col_{i}",
                    }
                    for i, name in enumerate(["day", "week"], 1)
                ],
                "keyColumnIds": ["day"],
                "attributeColumnIds": ["day", "week"],
                "labelColumnId": "day",
                "time": {
                    "baseGrain": "day",
                    "dateColumnId": "day",
                    "orderingColumnId": "day",
                },
            },
        },
    )
    create(
        client,
        {
            "objectId": "base",
            "kind": "planningLevel",
            "name": "Daily",
            "displayName": "Daily",
            "definition": {
                "population": {"operation": "source", "sourceId": pop},
                "grain": {
                    "keyIds": ["product", "day"],
                    "columns": [
                        {
                            "grainColumnId": name,
                            "name": name,
                            "populationRef": {"columnId": f"col_{i}"},
                        }
                        for i, name in enumerate(["product", "day"], 1)
                    ],
                },
                "dimensionBindings": [
                    {
                        "dimensionId": "Time",
                        "keyMappings": [
                            {"grainColumnId": "day", "dimensionColumnId": "day"}
                        ],
                        "selectedAttributeColumnIds": ["week"],
                    }
                ],
            },
        },
    )
    create(
        client,
        {
            "objectId": "pt",
            "kind": "planningLevel",
            "name": "Weekly",
            "displayName": "Weekly",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourcePlanningLevelId": "base",
                },
                "grain": {
                    "keyIds": ["product", "week"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": "product"},
                        },
                        {
                            "grainColumnId": "week",
                            "name": "week",
                            "populationRef": {
                                "sourceOutputColumnId": "da_"
                                + digest(["base", "Time", "week"])[:28]
                            },
                        },
                    ],
                },
                "dimensionBindings": [
                    {
                        "dimensionId": "Time",
                        "keyMappings": [
                            {"grainColumnId": "week", "dimensionColumnId": "week"}
                        ],
                        "selectedAttributeColumnIds": [],
                    }
                ],
            },
        },
    )
    rev = client.get(BASE).json()
    assert (
        client.put(
            BASE,
            json={"basePlanningLevelId": "base", "changeReason": ""},
            headers={"If-Match": str(rev["version"])},
        ).status_code
        == 200
    )
    source = managed(
        client, "WeeklyValues", b"product,week,quantity\nP1,W1,100\nP1,W2,90\n"
    )
    transaction(
        client, "weekly_values", source, "pt", [("product", "col_1"), ("week", "col_2")]
    )
    measure(
        client,
        "QTY",
        "weekly_values",
        "pt",
        "col_3",
        agg={**AGG, "nonTime": "sum", "time": time_method},
        mode="weighted_by_self",
    )
    for lid in ["base", "pt"]:
        assert build(client, lid)["state"] == "succeeded"
    run = build_values(client)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert [float(row[cols["QTY"]]) for row in finer["rows"]] == expected
    native, cols = values(client, "pt")
    assert [float(row[cols["QTY"]]) for row in native["rows"]] == [100, 90]


@pytest.mark.parametrize(
    "method,expected_native", [("count", 2), ("count_distinct", 1)]
)
def test_count_aggregations_repeat_during_initialisation_without_reinterpreting_count_semantics(
    clickhouse_app, method, expected_native
):
    client, _, _, _ = clickhouse_app
    setup(
        client,
        sales=b"P1,100\nP1,200\nP1,300\n",
        mode="equal",
        agg={**AGG, "nonTime": method},
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert [float(row[cols["SQTY"]]) for row in finer["rows"]] == [3, 3]
    native, cols = values(client, "pt")
    assert float(native["rows"][0][cols["SQTY"]]) == expected_native


def test_six_place_storage_and_coarser_values_use_rounded_base(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    setup(client, sales=b"P1,1\n", mode="weighted_by_self")
    admin.command(f"INSERT INTO `{database}`.`Population` VALUES ('P1','L3')")
    assert build(client, "base")["state"] == "succeeded"
    assert build(client, "pt")["state"] == "succeeded"
    assert_post(
        client,
        "/measures",
        {
            "objectId": "Seventh",
            "kind": "measure",
            "name": "Seventh",
            "displayName": "Seventh",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativePlanningLevelId": "base",
                "calculationType": "CalculateFirst",
                "aggregation": AGG,
                "resultType": {"type": "number", "nullable": True},
                "formula": {
                    "displayText": "1/7",
                    "expression": {
                        "type": "binary",
                        "operator": "divide",
                        "left": {"type": "literal", "value": 1},
                        "right": {"type": "literal", "value": 7},
                    },
                },
            },
        },
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    finer, cols = values(client, "base")
    assert [row[cols["SQTY"]] for row in finer["rows"]] == ["0.333333"] * 3
    assert [row[cols["Seventh"]] for row in finer["rows"]] == ["0.142857"] * 3
    native, cols = values(client, "pt")
    # Do not secretly balance a remainder. Coarser values aggregate the actual
    # six-place Base representation rather than a hidden full-precision version.
    assert native["rows"][0][cols["SQTY"]] == "0.999999"
    assert native["rows"][0][cols["Seventh"]] == "0.428571"
