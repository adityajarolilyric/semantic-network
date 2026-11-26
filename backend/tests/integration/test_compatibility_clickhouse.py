# ruff: noqa: F811
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_decision_view_clickhouse import seed_view, field, query_view
from .test_measures_clickhouse import BASE, update_measure, build_values

pytestmark = pytest.mark.integration


def test_ready_graph_uses_the_same_source_as_pivot_and_invalidates_on_change(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    seed_view(client)
    request = {
        "measures": ["FQTY", "SQTY", "ErrorAF"],
        "view": {
            "rows": [field("Product", "brand")],
            "columns": [field("Location", "zone")],
            "measures": ["FQTY", "SQTY", "ErrorAF"],
        },
    }
    response = client.post(BASE + "/compatibility", json=request)
    assert response.status_code == 200, response.text
    graph = response.json()
    context = graph["contexts"][0]["selection"]
    assert context["status"] == "ready", context
    pivot = query_view(
        client,
        rows=request["view"]["rows"],
        columns=request["view"]["columns"],
        measures=request["measures"],
    )
    assert pivot.status_code == 200, pivot.text
    assert context["sourceDimensionId"] == pivot.json()["plan"]["dimensionId"]
    rebuilt = build_values(client)
    assert rebuilt["state"] == "succeeded", rebuilt
    fresh = client.post(BASE + "/compatibility", json=request).json()
    assert fresh["generationId"] != graph["generationId"]
    assert fresh["contexts"][0]["selection"]["status"] == "ready"
    # Cached graph data must follow metadata changes; this read must not execute SQL.
    update_measure(
        client,
        "FQTY",
        lambda definition: definition.update({"missingTransactionValue": "zero"}),
    )
    updated = client.post(BASE + "/compatibility", json=request)
    assert updated.status_code == 200, updated.text
    assert (
        updated.json()["contexts"][0]["selection"]["status"] == "needs_materialisation"
    )
    assert updated.json()["generationId"] is None
