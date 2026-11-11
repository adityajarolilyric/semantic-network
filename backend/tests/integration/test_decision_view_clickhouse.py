# ruff: noqa: F811
import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import (
    AGG,
    assert_post,
    transaction,
    measure,
    build_values,
    update_measure,
)
from backend.app.engine.sql.contracts import digest

pytestmark = pytest.mark.integration


def dimension(client, oid, source, names):
    return assert_post(
        client,
        "/dimension-members",
        {
            "objectId": oid,
            "kind": "dimensionMembers",
            "name": oid,
            "displayName": oid,
            "isTimeDim": False,
            "definition": {
                "sourceId": source,
                "columns": [
                    {
                        "columnId": name,
                        "name": name,
                        "displayName": name.title(),
                        "sourceColumnId": f"col_{i}",
                    }
                    for i, name in enumerate(names, 1)
                ],
                "keyColumnIds": [names[0]],
                "attributeColumnIds": names,
                "labelColumnId": names[0],
            },
        },
    )


def calculated(client, oid, calculation):
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
                "measureType": "calculated",
                "nativeDimensionId": "base",
                "calculationType": calculation,
                "aggregation": AGG if calculation == "CalculateFirst" else None,
                "resultType": {"type": "number", "nullable": True},
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
            },
        },
    )


def seed_view(client, *, reporting=False):
    pop = managed(
        client, "Intersections", b"product,location\nP1,L1\nP1,L2\nP2,L1\nP3,L2\n"
    )
    products = managed(
        client, "Products", b"product,brand,color\nP1,A,red\nP2,A,blue\nP3,B,red\n"
    )
    locations = managed(client, "Locations", b"location,zone\nL1,Z1\nL2,Z2\n")
    dimension(client, "Product", products, ["product", "brand", "color"])
    dimension(client, "Location", locations, ["location", "zone"])
    create(
        client,
        {
            "objectId": "base",
            "kind": "dimension",
            "name": "PLC",
            "displayName": "PLC",
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
                "memberBindings": [
                    {
                        "memberCatalogId": "Product",
                        "keyMappings": [
                            {"grainColumnId": "product", "memberColumnId": "product"}
                        ],
                        "selectedAttributeColumnIds": ["brand"],
                    },
                    {
                        "memberCatalogId": "Location",
                        "keyMappings": [
                            {
                                "grainColumnId": "location",
                                "memberColumnId": "location",
                            }
                        ],
                        "selectedAttributeColumnIds": ["zone"],
                    },
                ],
            },
        },
    )
    create(
        client,
        {
            "objectId": "pt",
            "kind": "dimension",
            "name": "PT",
            "displayName": "PT",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourceDimensionId": "base",
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
    create(
        client,
        {
            "objectId": "brand_zone",
            "kind": "dimension",
            "name": "BrandZone",
            "displayName": "BrandZone",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourceDimensionId": "base",
                },
                "grain": {
                    "keyIds": ["brand", "zone"],
                    "columns": [
                        {
                            "grainColumnId": "brand",
                            "name": "brand",
                            "populationRef": {
                                "sourceOutputColumnId": "da_"
                                + digest(["base", "Product", "brand"])[:28]
                            },
                        },
                        {
                            "grainColumnId": "zone",
                            "name": "zone",
                            "populationRef": {
                                "sourceOutputColumnId": "da_"
                                + digest(["base", "Location", "zone"])[:28]
                            },
                        },
                    ],
                },
            },
        },
    )
    rev = client.get(BASE).json()
    assert (
        client.put(
            BASE,
            json={"baseDimensionId": "base", "changeReason": ""},
            headers={"If-Match": str(rev["version"])},
        ).status_code
        == 200
    )
    f = managed(
        client,
        "Forecast",
        b"product,location,quantity\nP1,L1,10.0\nP1,L2,30.0\nP2,L1,100.0\nP3,L2,-10.0\n",
    )
    s = managed(client, "Sales", b"product,quantity\nP1,120.0\nP2,80.0\nP3,20.0\n")
    transaction(
        client, "forecast", f, "base", [("product", "col_1"), ("location", "col_2")]
    )
    transaction(client, "sales", s, "pt", [("product", "col_1")])
    if reporting:
        # Reporting representations are built only at Base/Transaction-native
        # Dimensions. Declare this context explicitly rather than relying on the
        # retired behaviour of materialising every lookup Dimension.
        reporting = managed(
            client, "BrandZoneReporting", b"brand,zone\nA,Z1\nA,Z2\nB,Z2\n"
        )
        transaction(
            client,
            "brand_zone_reporting",
            reporting,
            "brand_zone",
            [("brand", "col_1"), ("zone", "col_2")],
        )
    measure(client, "FQTY", "forecast", "base", "col_3")
    measure(client, "SQTY", "sales", "pt", "col_2")
    measure(
        client,
        "AVGF",
        "forecast",
        "base",
        "col_3",
        agg={**AGG, "nonTime": "avg", "time": "avg"},
    )
    calculated(client, "ErrorAF", "AggregateFirst")
    calculated(client, "ErrorCF", "CalculateFirst")
    for level in ["base", "pt", "brand_zone"]:
        run = build(client, level)
        assert run["state"] == "succeeded", run
    run = build_values(client)
    assert run["state"] == "succeeded", run


def field(dim, col):
    return {"memberCatalogId": dim, "columnId": col}


def query_view(client, rows=(), columns=(), measures=("FQTY",)):
    return client.post(
        BASE + "/decision-view/query",
        json={"rows": list(rows), "columns": list(columns), "measures": list(measures)},
    )


def test_decision_view_exact_coarser_and_formula_semantics(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client, reporting=True)
    response = query_view(
        client,
        [field("Product", "brand")],
        [field("Location", "zone")],
        ["FQTY", "SQTY", "ErrorAF", "ErrorCF"],
    )
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["plan"]["dimensionId"] == "brand_zone"
    assert view["plan"]["exactGrain"]
    assert view["rows"] == [
        ["A", "Z1", "110.0", "140.0", "30.0", "70.0"],
        ["A", "Z2", "30.0", "60.0", "30.0", "30.0"],
        ["B", "Z2", "-10.0", "20.0", "30.0", "30.0"],
    ]


def test_decision_view_further_rollup_recalculates_aggregate_first(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client, reporting=True)
    response = query_view(
        client,
        [field("Product", "brand")],
        measures=["FQTY", "SQTY", "ErrorAF", "ErrorCF"],
    )
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["plan"]["dimensionId"] == "brand_zone"
    assert view["rows"] == [
        ["A", "140.0", "200.0", "60.0", "100.0"],
        ["B", "-10.0", "20.0", "30.0", "30.0"],
    ]
    assert (
        next(m for m in view["measures"] if m["measureId"] == "ErrorAF")["calculation"]
        == "recalculated"
    )


def test_decision_view_average_of_unequal_groups_falls_back_to_base(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client, reporting=True)
    response = query_view(client, [field("Product", "brand")], measures=["AVGF"])
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["plan"]["usesBase"]
    assert float(view["rows"][0][1]) == pytest.approx(140 / 3)
    # Exact stored average is safe to reuse at its own grain.
    exact = query_view(
        client, [field("Product", "brand")], [field("Location", "zone")], ["AVGF"]
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["plan"]["dimensionId"] == "brand_zone"
    assert exact.json()["rows"][0] == ["A", "Z1", "55.0"]


def test_decision_view_base_and_unexposed_population_dimension_lookup(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    response = query_view(
        client, [field("Product", "product")], [field("Location", "zone")]
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["usesBase"]
    assert len(response.json()["rows"]) == 4
    # Color is exposed in the DimensionMembers, but not copied into the planning population.
    lookup = query_view(client, [field("Product", "color")])
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["rows"] == [["blue", "100.0"], ["red", "30.0"]]
    options = client.get(BASE + "/decision-view/options")
    assert options.status_code == 200
    assert any(dimension["columns"] for dimension in options.json()["dimensions"])

    async def leases():
        return await client.app.state.container.store.repository.list(
            "engine_read_leases", {}, key="readId"
        )

    assert client.portal.call(leases) == []


def test_decision_view_global_total_and_size_errors(clickhouse_app):
    client, _, _, settings = clickhouse_app
    seed_view(client)
    response = query_view(client, measures=["FQTY", "SQTY", "ErrorAF", "ErrorCF"])
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [["130.0", "220.0", "90.0", "130.0"]]
    settings.decision_view_max_groups = 1
    limited = query_view(
        client, [field("Product", "product")], [field("Location", "zone")]
    )
    assert limited.status_code == 422, limited.text
    assert limited.json()["error"]["code"] == "decision_view_limit"
    settings.decision_view_max_groups = 5000
    settings.decision_view_max_cells = 1
    assert (
        query_view(
            client, [field("Product", "product")], [field("Location", "zone")]
        ).status_code
        == 422
    )


def test_decision_view_stale_network_and_bad_selections(clickhouse_app):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    assert query_view(client, [field("Product", "not_exposed")]).status_code == 422
    assert (
        query_view(
            client, [field("Product", "product")], [field("Product", "product")]
        ).status_code
        == 422
    )
    assert query_view(client, measures=["missing"]).status_code == 422
    update_measure(
        client, "FQTY", lambda d: d.update(aggregation={**AGG, "nonTime": "max"})
    )
    stale = query_view(client, [field("Product", "brand")])
    assert stale.status_code == 409, stale.text
    assert "Build Semantic Network" in stale.json()["error"]["message"]


@pytest.mark.parametrize(
    "non_time,time_method,order,expected",
    [
        ("sum", "last", "nonTimeThenTime", "100.0"),
        ("sum", "last", "timeThenNonTime", "130.0"),
        ("avg", "sum", "nonTimeThenTime", "120.0"),
        ("avg", "sum", "timeThenNonTime", "70.0"),
    ],
)
def test_decision_view_time_and_non_time_order(
    clickhouse_app, non_time, time_method, order, expected
):
    # Reuse the tested execution fixture, then query its Base population at month grain.
    from .test_measures_clickhouse import test_time_and_non_time_policies_and_order

    client, _, _, _ = clickhouse_app
    test_time_and_non_time_policies_and_order(
        clickhouse_app, non_time, time_method, order, expected
    )
    response = query_view(client, [field("dim_time", "month")], measures=["QTY"])
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [["January", expected]]


def test_edit_basis_missing_does_not_change_initial_finer_views(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    seed_view(client)
    # Exercise the multi-formula coverage graph with the production Compose
    # budget; all existing regressions still use the standalone 512 MiB cap.
    client.app.state.container.settings.planning_max_bytes = 2_147_483_648
    # Native Product totals can roll to Brand only with an explicit Product
    # binding; projection lineage alone does not prove extra attribute lookups.
    current = client.get(BASE + "/dimensions/pt").json()
    payload = {
        k: v
        for k, v in current.items()
        if k not in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
    }
    payload["definition"]["memberBindings"] = [
        {
            "memberCatalogId": "Product",
            "keyMappings": [{"grainColumnId": "product", "memberColumnId": "product"}],
            "selectedAttributeColumnIds": [],
        }
    ]
    response = client.put(
        BASE + "/dimensions/pt",
        json=payload,
        headers={"If-Match": str(current["version"])},
    )
    assert response.status_code == 200, response.text
    assert build(client, "pt", version=current["version"] + 1)["state"] == "succeeded"
    # Preserve the valid population but only provide one Forecast weight.
    admin.command(f"TRUNCATE TABLE `{database}`.`Forecast`")
    admin.command(f"INSERT INTO `{database}`.`Forecast` VALUES ('P1','L1',10)")
    update_measure(
        client,
        "SQTY",
        lambda d: d.update(allocation={"mode": "weighted", "basisMeasureId": "FQTY"}),
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    native = query_view(client, [field("Product", "product")], measures=["SQTY"])
    assert native.status_code == 200, native.text
    assert native.json()["rows"] == [["P1", "120.0"], ["P2", "80.0"], ["P3", "20.0"]]
    brand = query_view(client, [field("Product", "brand")], measures=["SQTY"])
    assert brand.status_code == 200, brand.text
    assert brand.json()["rows"] == [["A", "200.0"], ["B", "20.0"]]
    # Initial Sales uses its SUM aggregation, independent of missing edit-basis values.
    finer = query_view(
        client, [field("Location", "zone")], measures=["SQTY", "ErrorAF"]
    )
    assert finer.status_code == 200, finer.text
    assert finer.json()["rows"] == [["Z1", "140.0", "130.0"], ["Z2", "80.0", None]]
    assert not finer.json()["warnings"]
    exact = query_view(
        client,
        [field("Product", "product"), field("Location", "location")],
        measures=["SQTY"],
    )
    assert exact.status_code == 200, exact.text
    assert exact.json()["rows"] == [
        ["P1", "L1", "60.0"],
        ["P1", "L2", "60.0"],
        ["P2", "L1", "80.0"],
        ["P3", "L2", "20.0"],
    ]
