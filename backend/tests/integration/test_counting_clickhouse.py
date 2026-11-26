# ruff: noqa: F811
import pytest
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import assert_post, build_values
from .test_typed_measures_clickhouse import ref

pytestmark = pytest.mark.integration


def test_language_counts_materialisation_and_pivot(clickhouse_app):
    client, _, _, _ = clickhouse_app
    sid = managed(
        client,
        "CountFacts",
        b"product,location,qty,status\nP1,L1,100,Healthy\nP1,L2,0,At Risk\nP2,L1,,At Risk\nP2,L2,-5,At Risk\n",
    )
    response = client.post(
        BASE + "/transactions/from-grain",
        json={
            "objectId": "facts",
            "name": "Facts",
            "displayName": "Facts",
            "sourceId": sid,
            "grainColumnIds": ["col_1", "col_2"],
        },
    )
    assert response.status_code == 201, response.text
    level = response.json()["dimension"]
    lid = level["objectId"]
    product, location = level["definition"]["grain"]["keyIds"]
    create(
        client,
        {
            "objectId": "products",
            "name": "Products",
            "displayName": "Products",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourceDimensionId": lid,
                },
                "grain": {
                    "keyIds": [product],
                    "columns": [
                        {
                            "grainColumnId": product,
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": product},
                        }
                    ],
                },
            },
        },
    )
    assert build(client, lid)["state"] == "succeeded"
    assert build(client, "products")["state"] == "succeeded"
    ref(client, "Qty", "facts", lid, "col_3", "sum")
    ref(client, "Status", "facts", lid, "col_4", "single_value")
    from .test_measures_clickhouse import update_measure

    update_measure(
        client,
        "Qty",
        lambda d: d.update({"editable": True, "allocation": {"mode": "equal"}}),
    )
    formulas = {
        "Present": "COUNT([Qty])",
        "Positive": "COUNT_IF([Qty] > 0)",
        "Rows": "COUNT_ROWS()",
        "DistinctStatus": "COUNT_DISTINCT([Status])",
        "Ratio": "COUNT_IF([Qty] > 0) / COUNT_ROWS()",
    }
    for oid, text in formulas.items():
        assert_post(
            client,
            "/measures",
            {
                "objectId": oid,
                "name": oid,
                "displayName": oid,
                "business": {},
                "definition": {
                    "measureType": "calculated",
                    "nativeDimensionId": lid,
                    "calculationType": "AggregateFirst",
                    "formula": {"displayText": text, "languageVersion": "planning-v1"},
                },
            },
        )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    query = {
        "rows": [{"dimensionId": lid, "columnId": product}],
        "measures": list(formulas),
    }
    response = client.post(BASE + "/decision-view/query", json=query)
    assert response.status_code == 200, response.text
    rows = {row[0]: [float(v) for v in row[1:]] for row in response.json()["rows"]}
    assert rows["P1"] == [2, 1, 2, 2, 0.5]
    assert rows["P2"] == [1, 0, 2, 1, 0]
    response = client.post(
        BASE + "/decision-view/query", json={"rows": [], "measures": list(formulas)}
    )
    assert response.status_code == 200, response.text
    assert [float(v) for v in response.json()["rows"][0]] == [3, 1, 4, 2, 0.25]
    # Exact finer counting grain recalculates rather than distributing native counts.
    query["rows"].append({"dimensionId": lid, "columnId": location})
    response = client.post(BASE + "/decision-view/query", json=query)
    assert response.status_code == 200, response.text
    assert len(response.json()["rows"]) == 4

    from .test_managed_clickhouse import await_run
    from uuid import uuid4

    edit_view = {"rows": query["rows"], "measures": ["Qty"]}
    context = client.post(BASE + "/decision-view/query", json=edit_view).json()[
        "editContext"
    ]
    run = await_run(
        client,
        BASE,
        client.post(
            BASE + "/decision-view/edits",
            json={
                "view": edit_view,
                "measureId": "Qty",
                "memberValues": ["P1", "L1"],
                "value": -10,
                **context,
            },
            headers={"Idempotency-Key": uuid4().hex},
        ),
    )
    assert run["state"] == "succeeded", run
    response = client.post(
        BASE + "/decision-view/query", json={"rows": [], "measures": ["Positive"]}
    )
    assert response.status_code == 200, response.text
    assert float(response.json()["rows"][0][0]) == 0
    assert_post(
        client,
        "/measures",
        {
            "objectId": "Negative",
            "name": "Negative",
            "displayName": "Negative",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": lid,
                "calculationType": "AggregateFirst",
                "formula": {
                    "displayText": "COUNT_IF([Qty] < 0) + COUNT_DISTINCT([Products.product]) * 0",
                    "languageVersion": "planning-v1",
                },
            },
        },
    )
    revision = client.get(BASE).json()
    run = await_run(
        client,
        BASE,
        client.post(
            BASE + "/measures/execution/build",
            json={"mode": "incremental"},
            headers={
                "If-Match": str(revision["version"]),
                "Idempotency-Key": uuid4().hex,
            },
        ),
    )
    assert run["state"] == "succeeded", run
    response = client.post(
        BASE + "/decision-view/query",
        json={"rows": [], "measures": ["Negative", "Positive"]},
    )
    assert response.status_code == 200, response.text
    assert [float(v) for v in response.json()["rows"][0]] == [2, 0]

    assert_post(
        client,
        "/measures",
        {
            "objectId": "CoarseStatusCount",
            "name": "CoarseStatusCount",
            "displayName": "CoarseStatusCount",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": "products",
                "calculationType": "AggregateFirst",
                "formula": {
                    "displayText": "COUNT([Status])",
                    "languageVersion": "planning-v1",
                },
            },
        },
    )
    revision = client.get(BASE).json()
    run = await_run(
        client,
        BASE,
        client.post(
            BASE + "/measures/execution/build",
            json={"mode": "incremental"},
            headers={
                "If-Match": str(revision["version"]),
                "Idempotency-Key": uuid4().hex,
            },
        ),
    )
    assert run["state"] == "succeeded", run
    response = client.post(
        BASE + "/decision-view/query",
        json={"rows": [], "measures": ["CoarseStatusCount"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [[None]]
    assert response.json()["cellStates"] == [["mixed"]]

    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [{"dimensionId": lid, "columnId": product}],
            "measures": ["CoarseStatusCount", "Positive"],
        },
    )
    assert response.status_code == 200, response.text
    rows = {row[0]: row[1:] for row in response.json()["rows"]}
    assert rows["P1"] == [None, "0.0"]
    assert rows["P2"] == ["1.0", "0.0"]

    # Counts at Product cannot be distributed to Product x Location.
    response = client.post(
        BASE + "/decision-view/query",
        json={
            "rows": [
                {"dimensionId": lid, "columnId": product},
                {"dimensionId": lid, "columnId": location},
            ],
            "measures": ["CoarseStatusCount"],
        },
    )
    assert response.status_code == 422, response.text
