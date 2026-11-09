from copy import deepcopy
from backend.app.engine.graph.compatibility import MeasureCompatibilityAnalyzer
from .conftest import ROOT, EXAMPLE


def test_graph_before_materialisation_is_read_only_and_explains_paths(populated):
    before = populated.get(ROOT + "/measures").json()
    response = populated.post(
        ROOT + "/compatibility",
        json={"measures": ["measure_fqty", "measure_sqty"], "dimensionId": "pl_pt"},
    )
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph["contexts"][0]["selection"]["status"] == "needs_materialisation"
    assert graph["generationId"] is None
    assert {item["name"] for item in graph["selection"]} == {"FQTY", "SQTY"}
    assert populated.get(ROOT + "/measures").json() == before


def test_graph_rejects_unproven_mapping_and_bad_requests(populated):
    response = populated.post(
        ROOT + "/dimensions",
        json={
            "objectId": "independent_qty",
            "name": "Independent Qty",
            "displayName": "Independent Qty",
            "definition": {
                "population": {"operation": "source", "sourceId": "src_forecast"},
                "grain": {
                    "keyIds": ["quantity"],
                    "columns": [
                        {
                            "grainColumnId": "quantity",
                            "name": "quantity",
                            "populationRef": {"columnId": "forecast_qty"},
                        }
                    ],
                },
                "memberBindings": [],
            },
        },
    )
    assert response.status_code == 201, response.text
    graph = populated.post(
        ROOT + "/compatibility",
        json={"measures": ["measure_fqty"], "dimensionId": "independent_qty"},
    ).json()
    assert graph["contexts"][0]["selection"]["status"] == "unavailable"
    assert "FQTY" in graph["contexts"][0]["selection"]["reason"]
    assert (
        populated.post(
            ROOT + "/compatibility", json={"measures": ["missing"]}
        ).status_code
        == 422
    )
    assert (
        populated.post(
            ROOT + "/compatibility", json={"measures": ["measure_fqty", "measure_fqty"]}
        ).status_code
        == 422
    )
    assert (
        populated.post(ROOT + "/compatibility", json={"measureLimit": 25}).status_code
        == 422
    )


def test_graph_supports_empty_view_selection_and_pagination(populated):
    request = {
        "measures": [],
        "view": {"rows": [], "columns": [], "measures": []},
        "measureLimit": 1,
    }
    graph = populated.post(ROOT + "/compatibility", json=request).json()
    assert graph["contexts"][0]["name"] == "Selected pivot dimension_members"
    assert len(graph["measures"]) == 1
    assert graph["hasMoreMeasures"]
    graph = populated.post(
        ROOT + "/compatibility", json={"search": "fqty", "measureLimit": 1}
    ).json()
    assert graph["totalMeasures"] == 1
    assert graph["measures"][0]["name"] == "FQTY"


def test_shared_analyzer_respects_counts_typed_values_and_formula_dependencies():
    objects = deepcopy(EXAMPLE["semantic_objects"])
    by_id = {obj["objectId"]: obj for obj in objects}
    status = deepcopy(by_id["measure_sqty"])
    status.update(objectId="status", name="Status")
    status["definition"]["resultType"] = {"type": "string", "nullable": True}
    count = deepcopy(by_id["measure_error"])
    count.update(objectId="count", name="Count")
    count["definition"]["nativeDimensionId"] = "pl_pt"
    count["definition"]["formula"]["expression"] = {
        "type": "count",
        "function": "count_rows",
        "arguments": [],
    }
    owner = deepcopy(by_id["measure_error"])
    owner.update(objectId="derived_status", name="Derived Status")
    owner["definition"]["resultType"] = {"type": "string", "nullable": True}
    owner["definition"]["formula"]["expression"] = {
        "type": "measureRef",
        "measureId": "status",
    }
    analyzer = MeasureCompatibilityAnalyzer([*objects, status, count, owner], "pl_plt")
    assert not analyzer.outcome("status", "pl_plt").available
    assert not analyzer.outcome("count", "pl_plt").available
    assert not analyzer.outcome("derived_status", "pl_plt").available
    assert analyzer.outcome("count", "pl_pt").available


def test_complete_selection_is_checked_instead_of_pairwise_links(populated):
    revision = populated.get(ROOT).json()
    response = populated.put(
        ROOT,
        json={"baseDimensionId": None, "changeReason": "Independent contexts"},
        headers={"If-Match": str(revision["version"])},
    )
    assert response.status_code == 200, response.text

    def post(path, payload):
        response = populated.post(ROOT + path, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    for lid, keys in [
        ("ab", ["product_id", "location_id"]),
        ("ac", ["product_id", "date"]),
        ("bc", ["location_id", "date"]),
        ("a", ["product_id"]),
        ("b", ["location_id"]),
        ("c", ["date"]),
    ]:
        post(
            "/dimensions",
            {
                "objectId": lid,
                "name": lid,
                "displayName": lid,
                "definition": {
                    "population": {"operation": "source", "sourceId": "src_forecast"},
                    "grain": {
                        "keyIds": keys,
                        "columns": [
                            {
                                "grainColumnId": key,
                                "name": key,
                                "populationRef": {"columnId": key},
                            }
                            for key in keys
                        ],
                    },
                    "memberBindings": [],
                },
            },
        )
        if len(keys) == 2:
            post(
                "/transactions",
                {
                    "objectId": "tx_" + lid,
                    "name": "tx_" + lid,
                    "displayName": "tx_" + lid,
                    "definition": {
                        "sourceId": "src_forecast",
                        "dimensionId": lid,
                        "keyMappings": [
                            {"sourceColumnId": key, "grainColumnId": key}
                            for key in keys
                        ],
                    },
                },
            )
            post(
                "/measures",
                {
                    "objectId": "m_" + lid,
                    "name": "m_" + lid,
                    "displayName": "m_" + lid,
                    "business": {},
                    "definition": {
                        "measureType": "referenced",
                        "transactionId": "tx_" + lid,
                        "nativeDimensionId": lid,
                        "valueColumnId": "forecast_qty",
                        "aggregation": {"nonTime": "sum", "time": "sum"},
                    },
                },
            )
    for pair, level in [
        (["m_ab", "m_ac"], "a"),
        (["m_ab", "m_bc"], "b"),
        (["m_ac", "m_bc"], "c"),
    ]:
        graph = populated.post(
            ROOT + "/compatibility", json={"measures": pair, "dimensionId": level}
        ).json()
        assert graph["contexts"][0]["selection"]["status"] == "needs_materialisation", (
            graph
        )
    graph = populated.post(
        ROOT + "/compatibility", json={"measures": ["m_ab", "m_ac", "m_bc"]}
    ).json()
    assert all(
        context["selection"]["status"] == "unavailable" for context in graph["contexts"]
    )
