# ruff: noqa: F811
from copy import deepcopy

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, level, table
from .test_managed_clickhouse import await_run

pytestmark = pytest.mark.integration


@pytest.fixture
def dimension_input(clickhouse_app):
    client, admin, database, _ = clickhouse_app
    client.app.state.container.warehouse_sources.upload_metadata = None
    source = managed(client, "Products", b"product,category\nP1,A\nP2,B\n")
    parent = level(source, "parent")
    create(client, parent)
    assert table(client, "parent")["state"] == "not_built"
    return client, admin, database, source, parent


def projection():
    return {
        "objectId": "child",
        "kind": "dimension",
        "name": "Child",
        "displayName": "Child",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourceDimensionId": "parent",
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
                    "columnId": "category",
                    "name": "category",
                    "populationRef": {"sourceOutputColumnId": "category"},
                }
            ],
            "memberBindings": [],
        },
    }


def save(client, definition, version=None, key="save_derived"):
    return client.post(
        BASE + "/dimensions/save-and-build",
        json={"definition": definition, "expectedVersion": version},
        headers={"Idempotency-Key": key},
    )


def test_projection_save_builds_parents_and_reuses_unchanged_population(
    dimension_input,
):
    client, admin, database, _, parent = dimension_input
    schema = client.post(BASE + "/dimensions/schema", json=parent)
    assert schema.status_code == 200, schema.text
    assert [col["name"] for col in schema.json()["columns"]] == ["product", "category"]
    assert table(client, "parent")["state"] == "not_built"
    payload = projection()
    response = save(client, payload)
    assert response.status_code == 202, response.text
    run_id = response.json()["runId"]
    run = await_run(client, BASE, response)
    assert run["state"] == "succeeded", run
    assert run["resourceKind"] == "dimensionSet"
    built = table(client, "child")
    assert built["state"] == "ready"
    assert built["rows"] == [["P1", "A"], ["P2", "B"]]
    assert table(client, "parent")["state"] == "ready"
    assert save(client, payload).json()["runId"] == run_id
    assert client.get(BASE + "/measures").json()["items"] == []
    assert (
        admin.command(
            f"SELECT count() FROM system.tables WHERE database='{database}' AND startsWith(comment,'semanticTable:')"
        )
        == 2
    )
    repeat = await_run(client, BASE, save(client, payload, 1, "save_unchanged"))
    assert repeat["state"] == "succeeded", repeat
    assert table(client, "child")["builtAt"] == built["builtAt"]
    changed = {
        **deepcopy(payload),
        "name": "Renamed Child",
        "displayName": "Renamed Child",
    }
    updated = await_run(client, BASE, save(client, changed, 1, "save_changed"))
    assert updated["state"] == "succeeded", updated
    assert client.get(BASE + "/dimensions/child").json()["version"] == 2
    assert table(client, "child")["state"] == "ready"
    assert (
        admin.command(
            f"SELECT count() FROM system.tables WHERE database='{database}' AND startsWith(comment,'semanticTable:')"
        )
        == 2
    )


def test_schema_of_unbuilt_parent_chain_and_multiple_table_save(dimension_input):
    client, _, _, source, _ = dimension_input
    child = projection()
    create(client, child)
    schema = client.post(BASE + "/dimensions/schema", json=child)
    assert schema.status_code == 200, schema.text
    assert {col["name"] for col in schema.json()["columns"]} == {"product", "category"}
    assert table(client, "parent")["state"] == "not_built"
    assert table(client, "child")["state"] == "not_built"
    time = managed(client, "Weeks", b"week\nW1\nW2\n")
    multiple = {
        "objectId": "multiple",
        "kind": "dimension",
        "name": "ProductWeek",
        "displayName": "ProductWeek",
        "definition": {
            "population": {
                "operation": "cartesian_product",
                "factors": [
                    {
                        "factorId": "products",
                        "sourceId": source,
                        "selectedColumnIds": ["col_1"],
                    },
                    {
                        "factorId": "weeks",
                        "sourceId": time,
                        "selectedColumnIds": ["col_1"],
                    },
                ],
            },
            "grain": {
                "keyIds": ["product", "week"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "product",
                        "populationRef": {"factorId": "products", "columnId": "col_1"},
                    },
                    {
                        "grainColumnId": "week",
                        "name": "week",
                        "populationRef": {"factorId": "weeks", "columnId": "col_1"},
                    },
                ],
            },
            "memberBindings": [],
        },
    }
    run = await_run(client, BASE, save(client, multiple, key="save_multiple"))
    assert run["state"] == "succeeded", run
    assert table(client, "multiple")["rowCount"] == 4
    # Unrelated saved Dimensions are not included in this command's build scope.
    assert table(client, "parent")["state"] == "not_built"
    assert table(client, "child")["state"] == "not_built"
