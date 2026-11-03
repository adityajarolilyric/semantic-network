# ruff: noqa: F811
from uuid import uuid4
import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_managed_clickhouse import await_run
from backend.tests.test_csv_sources import upload

pytestmark = pytest.mark.integration
BASE = "/api/v1/models/default_model/revisions/revision_1"


def managed(client, table, data):
    response = upload(client, data, table=table, key="csv_" + uuid4().hex)
    assert response.status_code == 201, response.text
    client.post("/api/v1/workspace/bootstrap")
    run = await_run(
        client,
        BASE,
        client.post(
            BASE + "/managed-tables/enable",
            json={"tableName": table},
            headers={"Idempotency-Key": uuid4().hex},
        ),
    )
    assert run["state"] == "succeeded", run
    return run["sourceId"]


def level(source, oid="pl_main", attribute=True):
    return {
        "objectId": oid,
        "kind": "planningLevel",
        "name": oid,
        "displayName": oid,
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
            "populationAttributes": [
                {
                    "columnId": "category",
                    "name": "category",
                    "populationRef": {"columnId": "col_2"},
                }
            ]
            if attribute
            else [],
            "dimensionBindings": [],
        },
    }


def create(client, payload):
    response = client.post(BASE + "/planning-levels", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def build(client, oid, version=1):
    return await_run(
        client,
        BASE,
        client.post(
            BASE + "/planning-levels/" + oid + "/build",
            headers={"If-Match": str(version), "Idempotency-Key": uuid4().hex},
        ),
    )


def table(client, oid):
    response = client.get(BASE + "/planning-levels/" + oid + "/table?preview=true")
    assert response.status_code == 200, response.text
    return response.json()


def test_source_attributes_build_and_stale_detection(clickhouse_app):
    client, _, _, _ = clickhouse_app
    source = managed(client, "Population", b"product,category\nP1,C1\nP2,C2\n")
    payload = level(source)
    create(client, payload)
    preview = client.post(BASE + "/planning-levels/preview/population", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimatedRows"] == 2
    result = build(client, "pl_main")
    assert result["state"] == "succeeded", result
    data = table(client, "pl_main")
    assert data["state"] == "ready" and data["rowCount"] == 2
    assert data["rows"] == [["P1", "C1"], ["P2", "C2"]]
    assert [c["role"] for c in data["columns"]] == ["key", "attribute"]
    payload["definition"]["populationAttributes"] = []
    response = client.put(
        BASE + "/planning-levels/pl_main", json=payload, headers={"If-Match": "1"}
    )
    assert response.status_code == 200, response.text
    stale = table(client, "pl_main")
    assert stale["state"] == "needs_rebuild" and stale["rows"] == data["rows"]
    assert build(client, "pl_main", 2)["state"] == "succeeded"
    assert table(client, "pl_main")["rows"] == [["P1"], ["P2"]]


def test_cartesian_collision_and_projection(clickhouse_app):
    client, _, _, _ = clickhouse_app
    a = managed(client, "Products", b"id,category\nP1,C1\nP2,C1\n")
    b = managed(client, "Time", b"id\n2026-01-01\n2026-01-02\n")
    payload = level(a)
    payload["definition"]["population"] = {
        "operation": "cartesian_product",
        "factors": [
            {
                "factorId": "products",
                "sourceId": a,
                "selectedColumnIds": ["col_1", "col_2"],
            },
            {"factorId": "time", "sourceId": b, "selectedColumnIds": ["col_1"]},
        ],
    }
    payload["definition"]["grain"] = {
        "keyIds": ["product", "time"],
        "columns": [
            {
                "grainColumnId": "product",
                "name": "id",
                "populationRef": {"factorId": "products", "columnId": "col_1"},
            },
            {
                "grainColumnId": "time",
                "name": "id",
                "populationRef": {"factorId": "time", "columnId": "col_1"},
            },
        ],
    }
    payload["definition"]["populationAttributes"][0]["populationRef"]["factorId"] = (
        "products"
    )
    create(client, payload)
    result = build(client, "pl_main")
    assert result["state"] == "succeeded", result
    data = table(client, "pl_main")
    assert data["rowCount"] == 4
    assert [c["name"] for c in data["columns"]][:2] == ["id", "Time__id"]
    child = {
        "objectId": "pl_product",
        "kind": "planningLevel",
        "name": "Product",
        "displayName": "Product",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourcePlanningLevelId": "pl_main",
            },
            "grain": {
                "keyIds": ["product"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "id",
                        "populationRef": {"sourceGrainColumnId": "product"},
                    }
                ],
            },
            "populationAttributes": [
                {
                    "columnId": "cat",
                    "name": "category",
                    "populationRef": {"sourceOutputColumnId": "category"},
                }
            ],
        },
    }
    create(client, child)
    result = build(client, "pl_product")
    assert result["state"] == "succeeded", result
    assert table(client, "pl_product")["rows"] == [["P1", "C1"], ["P2", "C1"]]


@pytest.mark.parametrize(
    "rows,expected",
    [(b"P1,C1\nP1,C2\n", "duplicate_planning_key"), (b",C1\n", "null_planning_key")],
)
def test_reject_invalid_grain(clickhouse_app, rows, expected):
    client, _, _, _ = clickhouse_app
    source = managed(client, "Population", b"product,category\n" + rows)
    create(client, level(source))
    run = build(client, "pl_main")
    assert run["state"] == "failed" and run["error"]["code"] == expected, run
    assert (
        client.get(BASE + "/planning-levels/pl_main/table").json()["rowCount"] is None
    )


@pytest.mark.parametrize(
    "dim_rows,expected",
    [
        (b"C1,Category\n", None),
        (b"C1,A\nC1,B\n", "ambiguous_dimension_lookup"),
        (b"C2,Other\n", "unmatched_dimension_member"),
    ],
)
def test_non_primary_attribute_lookup_and_population_attribute_mapping(
    clickhouse_app, dim_rows, expected
):
    client, _, _, _ = clickhouse_app
    source = managed(client, "Population", b"product,category\nP1,C1\n")
    dim_source = managed(client, "Categories", b"code,label\n" + dim_rows)
    dim = {
        "objectId": "dim_category",
        "kind": "dimension",
        "name": "Category",
        "displayName": "Category",
        "isTimeDim": False,
        "definition": {
            "sourceId": dim_source,
            "columns": [
                {
                    "columnId": "code",
                    "name": "code",
                    "displayName": "Code",
                    "sourceColumnId": "col_1",
                },
                {
                    "columnId": "label",
                    "name": "label",
                    "displayName": "Label",
                    "sourceColumnId": "col_2",
                },
            ],
            "keyColumnIds": ["label"],
            "labelColumnId": "label",
            "attributeColumnIds": ["code", "label"],
        },
    }
    response = client.post(BASE + "/dimensions", json=dim)
    assert response.status_code == 201, response.text
    payload = level(source)
    payload["definition"]["dimensionBindings"] = [
        {
            "dimensionId": "dim_category",
            "keyMappings": [
                {"populationColumnId": "category", "dimensionColumnId": "code"}
            ],
            "selectedAttributeColumnIds": ["label"],
        }
    ]
    create(client, payload)
    run = build(client, "pl_main")
    if expected:
        assert run["state"] == "failed" and run["error"]["code"] == expected, run
    else:
        assert run["state"] == "succeeded", run
        assert table(client, "pl_main")["rows"] == [["P1", "C1", "Category"]]


def test_row_limit_and_failed_build_retains_previous_table(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    source = managed(client, "Population", b"product,category\nP1,C1\n")
    create(client, level(source))
    assert build(client, "pl_main")["state"] == "succeeded"
    before = table(client, "pl_main")
    admin.command(f"INSERT INTO `{database}`.Population VALUES ('P1','C2')")
    run = build(client, "pl_main")
    assert run["error"]["code"] == "duplicate_planning_key", run
    assert table(client, "pl_main")["rows"] == before["rows"]
    client.app.state.container.planning_builds.executor.settings.planning_max_rows = 1
    run = build(client, "pl_main")
    assert run["error"]["code"] == "planning_row_limit", run


def test_previews_cap_at_100_without_truncating_materialized_table(clickhouse_app):
    client, _, _, _ = clickhouse_app
    source = managed(
        client,
        "Population",
        ("product,category\n" + "".join(f"P{i:03},C\n" for i in range(150))).encode(),
    )
    payload = level(source)
    create(client, payload)
    preview = client.post(BASE + "/planning-levels/preview/population", json=payload)
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["rows"]) == 100
    run = build(client, "pl_main")
    assert run["state"] == "succeeded", run
    result = table(client, "pl_main")
    assert result["rowCount"] == 150 and len(result["rows"]) == 100


def test_projection_rejects_attributes_that_vary_at_new_grain(clickhouse_app):
    client, _, _, _ = clickhouse_app
    source = managed(
        client, "Population", b"product,date,category\nP1,D1,C1\nP1,D2,C2\n"
    )
    payload = level(source)
    payload["definition"]["grain"]["keyIds"].append("date")
    payload["definition"]["grain"]["columns"].append(
        {
            "grainColumnId": "date",
            "name": "date",
            "populationRef": {"columnId": "col_2"},
        }
    )
    payload["definition"]["populationAttributes"][0]["populationRef"]["columnId"] = (
        "col_3"
    )
    create(client, payload)
    assert build(client, "pl_main")["state"] == "succeeded"
    child = {
        "objectId": "pl_product",
        "kind": "planningLevel",
        "name": "Product",
        "displayName": "Product",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourcePlanningLevelId": "pl_main",
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
            "populationAttributes": [
                {
                    "columnId": "cat",
                    "name": "category",
                    "populationRef": {"sourceOutputColumnId": "category"},
                }
            ],
        },
    }
    create(client, child)
    result = build(client, "pl_product")
    assert (
        result["state"] == "failed"
        and result["error"]["code"] == "duplicate_planning_key"
    ), result


def test_composite_dimension_lookup_and_scoped_filters(clickhouse_app):
    client, _, _, _ = clickhouse_app
    source = managed(
        client, "Population", b"product,code,region\nP1,C,East\nP2,C,West\n"
    )
    dim_source = managed(
        client, "Members", b"code,region,label\nC,East,Included\nC,West,Excluded\n"
    )
    dim = {
        "objectId": "dim_member",
        "kind": "dimension",
        "name": "Member",
        "displayName": "Member",
        "isTimeDim": False,
        "definition": {
            "sourceId": dim_source,
            "columns": [
                {
                    "columnId": cid,
                    "name": cid,
                    "displayName": cid,
                    "sourceColumnId": f"col_{i}",
                }
                for i, cid in enumerate(["code", "region", "label"], 1)
            ],
            "keyColumnIds": ["label"],
            "labelColumnId": "label",
            "attributeColumnIds": ["label"],
            "filters": {
                "root": {
                    "type": "group",
                    "combinator": "and",
                    "children": [
                        {
                            "type": "condition",
                            "columnId": "label",
                            "operator": "ne",
                            "values": ["Excluded"],
                        }
                    ],
                }
            },
        },
    }
    response = client.post(BASE + "/dimensions", json=dim)
    assert response.status_code == 201, response.text
    payload = level(source)
    payload["definition"]["populationAttributes"].append(
        {"columnId": "region", "name": "region", "populationRef": {"columnId": "col_3"}}
    )
    payload["definition"]["dimensionBindings"] = [
        {
            "dimensionId": "dim_member",
            "keyMappings": [
                {"populationColumnId": "category", "dimensionColumnId": "code"},
                {"populationColumnId": "region", "dimensionColumnId": "region"},
            ],
            "selectedAttributeColumnIds": ["label"],
        }
    ]
    payload["definition"]["filters"] = {
        "root": {
            "type": "group",
            "combinator": "and",
            "children": [
                {
                    "type": "condition",
                    "columnId": "region",
                    "operator": "eq",
                    "values": ["East"],
                }
            ],
        }
    }
    create(client, payload)
    result = build(client, "pl_main")
    assert result["state"] == "succeeded", result
    assert table(client, "pl_main")["rows"] == [["P1", "C", "East", "Included"]]


def test_attribute_grain_projection_membership_and_functional_enrichment(
    clickhouse_app,
):
    from backend.app.engine.contracts import digest
    from backend.app.engine.relationships import LevelRelationshipAnalyzer

    client, admin, database, _ = clickhouse_app
    population = managed(
        client,
        "PLCMatrix",
        b"product,location,customer\nP1,L1,C1\nP2,L2,C2\nP3,L3,C3\n",
    )
    definitions = [
        (
            "Product",
            ["product", "Brand", "Category"],
            b"product,Brand,Category\nP1,B1,Cat1\nP2,B1,Cat1\nP3,B2,Cat2\n",
            "Brand",
        ),
        (
            "Location",
            ["location", "Zone"],
            b"location,Zone\nL1,Z1\nL2,Z1\nL3,Z2\n",
            "Zone",
        ),
        (
            "Customer",
            ["customer", "Sold To"],
            b"customer,Sold To\nC1,S1\nC2,S1\nC3,S2\n",
            "Sold To",
        ),
    ]
    bindings, coarse_bindings, coarse_columns = [], [], []
    for name, names, rows, attribute in definitions:
        source = managed(client, name + "Members", rows)
        dim = {
            "objectId": "dim_" + name,
            "kind": "dimension",
            "name": name,
            "displayName": name,
            "isTimeDim": False,
            "definition": {
                "sourceId": source,
                "columns": [
                    {
                        "columnId": f"d{i}",
                        "name": n,
                        "displayName": n,
                        "sourceColumnId": f"col_{i}",
                    }
                    for i, n in enumerate(names, 1)
                ],
                "keyColumnIds": ["d1"],
                "labelColumnId": "d1",
                "attributeColumnIds": [f"d{i}" for i in range(1, len(names) + 1)],
            },
        }
        response = client.post(BASE + "/dimensions", json=dim)
        assert response.status_code == 201, response.text
        bindings.append(
            {
                "dimensionId": dim["objectId"],
                "keyMappings": [{"grainColumnId": names[0], "dimensionColumnId": "d1"}],
                "selectedAttributeColumnIds": ["d2"],
            }
        )
        coarse_bindings.append(
            {
                "dimensionId": dim["objectId"],
                "keyMappings": [{"grainColumnId": name, "dimensionColumnId": "d2"}],
                "selectedAttributeColumnIds": [],
            }
        )
        coarse_columns.append(
            {
                "grainColumnId": name,
                "name": attribute,
                "populationRef": {
                    "sourceOutputColumnId": "da_"
                    + digest(["plc", dim["objectId"], "d2"])[:28]
                },
            }
        )
    create(
        client,
        {
            "objectId": "plc",
            "kind": "planningLevel",
            "name": "PLC",
            "displayName": "PLC",
            "definition": {
                "population": {"operation": "source", "sourceId": population},
                "grain": {
                    "keyIds": ["product", "location", "customer"],
                    "columns": [
                        {
                            "grainColumnId": n,
                            "name": n,
                            "populationRef": {"columnId": f"col_{i}"},
                        }
                        for i, n in enumerate(["product", "location", "customer"], 1)
                    ],
                },
                "dimensionBindings": bindings,
            },
        },
    )
    rev = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"basePlanningLevelId": "plc", "changeReason": ""},
        headers={"If-Match": str(rev["version"])},
    )
    assert response.status_code == 200, response.text
    assert build(client, "plc")["state"] == "succeeded"
    child = {
        "objectId": "bzs",
        "kind": "planningLevel",
        "name": "BrandZoneSoldTo",
        "displayName": "BrandZoneSoldTo",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourcePlanningLevelId": "plc",
            },
            "grain": {
                "keyIds": ["Product", "Location", "Customer"],
                "columns": coarse_columns,
            },
            "dimensionBindings": coarse_bindings,
        },
    }
    # All bindings have multiple raw members for B1/Z1/S1. Membership must not multiply rows.
    create(client, child)
    result = build(client, "bzs")
    assert result["state"] == "succeeded", result
    assert table(client, "bzs")["rows"] == [["B1", "Z1", "S1"], ["B2", "Z2", "S2"]]
    objects = (
        client.get(BASE + "/planning-levels").json()["items"]
        + client.get(BASE + "/dimensions").json()["items"]
    )
    analyzer = LevelRelationshipAnalyzer(objects)
    assert analyzer.analyze("plc", "bzs")["relationship"] == "roll_up"
    assert analyzer.analyze("bzs", "plc")["relationship"] == "roll_down"

    child["definition"]["dimensionBindings"][0]["selectedAttributeColumnIds"] = ["d3"]
    response = client.put(
        BASE + "/planning-levels/bzs", json=child, headers={"If-Match": "1"}
    )
    assert response.status_code == 200, response.text
    result = build(client, "bzs", 2)
    assert result["state"] == "succeeded", result
    before = table(client, "bzs")
    assert before["rowCount"] == 2 and len(before["columns"]) == 4
    assert client.get(BASE + "/planning-levels/bzs").json()["definition"]["grain"][
        "keyIds"
    ] == ["Product", "Location", "Customer"]
    # Identical requested attributes across different products are fine; conflicting values are not.
    admin.command(
        f"INSERT INTO `{database}`.ProductMembers VALUES ('P4','B1','DifferentCategory')"
    )
    result = build(client, "bzs", 2)
    assert (
        result["state"] == "failed"
        and result["error"]["code"] == "ambiguous_dimension_lookup"
    ), result
    assert table(client, "bzs")["rows"] == before["rows"]
    # Removing enrichment returns to membership-only semantics, so the extra Category is ignored.
    child["definition"]["dimensionBindings"][0]["selectedAttributeColumnIds"] = []
    response = client.put(
        BASE + "/planning-levels/bzs", json=child, headers={"If-Match": "2"}
    )
    assert response.status_code == 200, response.text
    assert build(client, "bzs", 3)["state"] == "succeeded"
    # A value which does not occur in the mapped dimension column must still fail membership.
    child["definition"]["dimensionBindings"][0]["keyMappings"][0][
        "dimensionColumnId"
    ] = "d3"
    response = client.put(
        BASE + "/planning-levels/bzs", json=child, headers={"If-Match": "3"}
    )
    assert response.status_code == 200, response.text
    result = build(client, "bzs", 4)
    assert (
        result["state"] == "failed"
        and result["error"]["code"] == "unmatched_dimension_member"
    ), result


def test_deleted_planning_resource_is_collected_and_recreated_with_fresh_name(
    clickhouse_app,
):
    from datetime import timedelta
    from backend.app.engine.artifacts import ArtifactCollector
    from backend.app.services.base import now

    client, admin, database, _ = clickhouse_app
    source = managed(client, "Population", b"product,category\nP1,C1\n")
    payload = level(source)
    created = create(client, payload)
    assert build(client, created["objectId"])["state"] == "succeeded"
    container = client.app.state.container
    identity = {
        "modelId": "default_model",
        "revisionId": "revision_1",
        "resourceKind": "planningLevel",
        "resourceId": created["objectId"],
    }
    old = client.portal.call(
        container.store.repository.get, "engine_table_resources", identity
    )
    response = client.delete(
        BASE + "/planning-levels/" + created["objectId"],
        headers={"If-Match": str(created["version"])},
    )
    assert response.status_code == 204, response.text

    async def collect():
        repo = container.store.repository
        for artifact in await repo.list(
            "engine_artifacts", identity, key="artifactId", limit=100
        ):
            await repo.replace(
                "engine_artifacts",
                {"artifactId": artifact["artifactId"]},
                {**artifact, "createdAt": now() - timedelta(days=2)},
            )
        await ArtifactCollector(container.managed_tables).collect()
        return await repo.get("engine_table_resources", identity)

    assert client.portal.call(collect) is None
    assert admin.command(f"EXISTS TABLE `{database}`.`{old['physicalName']}`") == 0
    create(client, payload)
    assert build(client, created["objectId"])["state"] == "succeeded"
    new = client.portal.call(
        container.store.repository.get, "engine_table_resources", identity
    )
    assert new["physicalName"] != old["physicalName"]
    assert table(client, created["objectId"])["rows"] == [["P1", "C1"]]


@pytest.mark.parametrize("attribute", [False, True])
def test_source_final_distinct_ignores_unselected_columns(clickhouse_app, attribute):
    client, _, _, _ = clickhouse_app
    source = managed(
        client,
        "Population",
        b"product,category,ignored\nP1,C1,A\nP1,C1,B\nP2,,C\nP2,,D\n",
    )
    payload = level(source, attribute=attribute)
    create(client, payload)
    preview = client.post(BASE + "/planning-levels/preview/population", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimatedRows"] == 2
    run = build(client, "pl_main")
    assert run["state"] == "succeeded", run
    result = table(client, "pl_main")
    assert result["rowCount"] == 2
    assert result["rows"] == (
        [["P1", "C1"], ["P2", None]] if attribute else [["P1"], ["P2"]]
    )


def test_cartesian_daily_time_selected_as_week_distinct(clickhouse_app):
    client, _, _, _ = clickhouse_app
    population = managed(client, "ProductLocation", b"product,location\nP1,L1\nP2,L2\n")
    time = managed(
        client,
        "Time",
        b"day,week\n2026-01-01,W1\n2026-01-02,W1\n2026-01-08,W2\n2026-01-09,W2\n",
    )
    payload = level(population, attribute=False)
    payload["definition"]["population"] = {
        "operation": "cartesian_product",
        "factors": [
            {
                "factorId": "intersection",
                "sourceId": population,
                "selectedColumnIds": ["col_1", "col_2"],
            },
            {"factorId": "time", "sourceId": time, "selectedColumnIds": ["col_2"]},
        ],
    }
    payload["definition"]["grain"] = {
        "keyIds": ["product", "location", "week"],
        "columns": [
            {
                "grainColumnId": cid,
                "name": cid,
                "populationRef": {"factorId": factor, "columnId": col},
            }
            for cid, factor, col in [
                ("product", "intersection", "col_1"),
                ("location", "intersection", "col_2"),
                ("week", "time", "col_2"),
            ]
        ],
    }
    create(client, payload)
    # Eight raw Cartesian rows must fit a four-row distinct-output limit.
    settings = client.app.state.container.planning_builds.executor.settings
    previous_limit = settings.planning_max_rows
    settings.planning_max_rows = 4
    preview = client.post(BASE + "/planning-levels/preview/population", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimatedRows"] == 4
    run = build(client, "pl_main")
    assert run["state"] == "succeeded", run
    assert sorted(table(client, "pl_main")["rows"]) == [
        ["P1", "L1", "W1"],
        ["P1", "L1", "W2"],
        ["P2", "L2", "W1"],
        ["P2", "L2", "W2"],
    ]
    settings.planning_max_rows = previous_limit
    # Keeping Day as a non-key attribute conflicts with the Week grain even
    # after DISTINCT. Neither Preview nor Build may choose an arbitrary day.
    payload["definition"]["population"]["factors"][1]["selectedColumnIds"].append(
        "col_1"
    )
    payload["definition"]["populationAttributes"] = [
        {
            "columnId": "day",
            "name": "day",
            "populationRef": {"factorId": "time", "columnId": "col_1"},
        }
    ]
    preview = client.post(BASE + "/planning-levels/preview/population", json=payload)
    # Population preview can show the conflicting distinct rows; Build enforces
    # grain uniqueness before activating a physical planning table.
    assert preview.status_code == 200, preview.text
    assert preview.json()["estimatedRows"] == 8
    response = client.put(
        BASE + "/planning-levels/pl_main", json=payload, headers={"If-Match": "1"}
    )
    assert response.status_code == 200, response.text
    failed = build(client, "pl_main", version=2)
    assert failed["error"]["code"] == "duplicate_planning_key", failed
    assert table(client, "pl_main")["rowCount"] == 4


def test_projection_keeps_inherited_name_when_dimension_reselects_attribute(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    source = managed(client, "Population", b"product\nP1\nP2\n")
    products = managed(
        client, "Products", b"product_id,product_name\nP1,Apple\nP2,Pear\n"
    )
    dim = {
        "objectId": "dim_product",
        "kind": "dimension",
        "name": "Product",
        "displayName": "Product",
        "isTimeDim": False,
        "definition": {
            "sourceId": products,
            "columns": [
                {
                    "columnId": "id",
                    "name": "product_id",
                    "displayName": "Product ID",
                    "sourceColumnId": "col_1",
                },
                {
                    "columnId": "name",
                    "name": "product_name",
                    "displayName": "Product name",
                    "sourceColumnId": "col_2",
                },
            ],
            "keyColumnIds": ["id"],
            "labelColumnId": "name",
            "attributeColumnIds": ["id", "name"],
        },
    }
    response = client.post(BASE + "/dimensions", json=dim)
    assert response.status_code == 201, response.text
    binding = {
        "dimensionId": "dim_product",
        "keyMappings": [{"grainColumnId": "product", "dimensionColumnId": "id"}],
        "selectedAttributeColumnIds": ["name"],
    }
    parent = level(source, attribute=False)
    parent["definition"]["dimensionBindings"] = [binding]
    create(client, parent)
    assert build(client, "pl_main")["state"] == "succeeded"
    inherited = table(client, "pl_main")["columns"][1]["columnId"]
    child = level(source, oid="pl_child", attribute=False)
    child["definition"]["population"] = {
        "operation": "distinct_projection",
        "sourcePlanningLevelId": "pl_main",
    }
    child["definition"]["grain"]["columns"][0]["populationRef"] = {
        "sourceGrainColumnId": "product"
    }
    child["definition"]["populationAttributes"] = [
        {
            "columnId": "inherited_name",
            "name": "product_name",
            "populationRef": {"sourceOutputColumnId": inherited},
        }
    ]
    child["definition"]["dimensionBindings"] = [binding]
    create(client, child)
    run = build(client, "pl_child")
    assert run["state"] == "succeeded", run
    result = table(client, "pl_child")
    assert [c["name"] for c in result["columns"]] == [
        "product",
        "product_name",
        "Product__product_name",
    ]
    assert result["rows"] == [["P1", "Apple", "Apple"], ["P2", "Pear", "Pear"]]
    assert [c["columnId"] for c in result["columns"]][1] == "inherited_name"
    # Names change only at the alias boundary; rebuilding does not create new IDs
    # or append prefixes. Explicitly selected duplicate outputs remain addressable.
    assert build(client, "pl_child")["state"] == "succeeded"
    again = table(client, "pl_child")
    assert again["columns"] == result["columns"]
    assert again["rows"] == result["rows"]
