"""Exercise readable calculated names through real ClickHouse materialisation."""

import json

import pytest

from backend.app.warehouse.sql import quote_identifier
from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_measures_clickhouse import assert_post, build_values, setup, values
from .test_planning_clickhouse import BASE

pytestmark = pytest.mark.integration


def test_named_formulas_materialise_quoted_columns_and_dependencies(clickhouse_app):  # noqa: F811
    client, admin, database, _ = clickhouse_app
    setup(client)
    names = ["Absolute Error", "Error `quoted` \\ 世界", "Error [absolute]", "A = B"]
    for index, name in enumerate(names):
        assert_post(
            client,
            "/measures",
            {
                "objectId": f"named_{index}",
                "business": {},
                "formulaDefinition": f"{json.dumps(name)} = abs([FQTY] - [SQTY])",
                "definition": {
                    "measureType": "calculated",
                    "nativeDimensionId": "pt",
                    "calculationType": "AggregateFirst",
                },
            },
        )
    assert_post(
        client,
        "/measures",
        {
            "objectId": "double_error",
            "business": {},
            "formulaDefinition": "Double Error = [Error [absolute]]] * 2",
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": "pt",
                "calculationType": "AggregateFirst",
            },
        },
    )
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, columns = values(client, "pt")
    assert result["rowCount"] == 1
    for name in names:
        assert float(result["rows"][0][columns[name]]) == 20
    assert float(result["rows"][0][columns["Double Error"]]) == 40
    sql_response = client.get(BASE + "/measures/execution/levels/pt/output-sql")
    assert sql_response.status_code == 200, sql_response.text
    output_sql = sql_response.json()["sql"]
    # Assert the warehouse, not just API metadata, preserves these identifiers.
    assert (
        admin.command(
            "SELECT " + quote_identifier(names[1]) + " FROM (" + output_sql + ")"
        )
        == 20
    )
    assert admin.command("EXISTS DATABASE " + quote_identifier(database)) == 1
