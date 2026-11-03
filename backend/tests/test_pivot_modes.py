from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlglot import exp

from backend.app.core.errors import MetadataError
from backend.app.engine.compiler import ClickHouseCompiler
from backend.app.engine.planner import ManagedTablePlanner
from backend.app.schemas.managed_tables import ManagedDefinition
from backend.tests.test_engine import SOURCE, SPEC


def row_source():
    source = deepcopy(SOURCE)
    source["definition"]["columns"] = [
        {"columnId": cid, "name": cid, "physicalType": typ, "nullable": False}
        for cid, typ in [
            ("pa", "String"),
            ("pv", "String"),
            ("la", "String"),
            ("lv", "String"),
            ("week", "String"),
            ("qty", "Int64"),
        ]
    ]
    return source


def row_spec():
    return {
        "primaryKeyColumnIds": [],
        "pipeline": [
            {
                "stageId": "stage",
                "mode": "expand_rows",
                "groupByColumnIds": ["week", "qty", "pv", "lv"],
                "pairs": [
                    {
                        "pairId": "product",
                        "attributeColumnId": "pa",
                        "valueColumnId": "pv",
                        "outputs": [{"attributeValue": "brand", "name": "brand"}],
                    },
                    {
                        "pairId": "location",
                        "attributeColumnId": "la",
                        "valueColumnId": "lv",
                        "outputs": [{"attributeValue": "zone", "name": "zone"}],
                    },
                ],
            }
        ],
    }


def test_expand_outputs_exclude_consumed_columns_and_accept_generated_keys():
    source, spec = row_source(), row_spec()
    first = ManagedTablePlanner().plan(source, ManagedDefinition.model_validate(spec))
    assert [c["name"] for c in first.schema.columns] == ["week", "qty", "brand", "zone"]
    spec["primaryKeyColumnIds"] = [
        "week",
        *[c["columnId"] for c in first.schema.columns[2:]],
    ]
    plan = ManagedTablePlanner().plan(source, ManagedDefinition.model_validate(spec))
    assert plan.definition["pipeline"][0]["groupByColumnIds"] == ["week", "qty"]
    compiled = ClickHouseCompiler("semantic").compile(plan)
    assert "GROUP BY" not in compiled.sql.upper()
    assert "DISTINCT" not in compiled.sql.upper()
    assert [c.code for c in compiled.checks] == ["duplicate_managed_key"]
    assert [c["columnId"] for c in first.schema.columns] == [
        c["columnId"] for c in plan.schema.columns
    ]
    spec["primaryKeyColumnIds"] = ["pv"]
    with pytest.raises(MetadataError, match="effective output"):
        ManagedTablePlanner().plan(source, ManagedDefinition.model_validate(spec))


def test_expand_allows_no_retained_columns_and_quotes_shadowing_aliases():
    spec = row_spec()
    spec["pipeline"][0]["groupByColumnIds"] = []
    spec["pipeline"][0]["pairs"][0]["outputs"][0]["name"] = "pv"
    plan = ManagedTablePlanner().plan(
        row_source(), ManagedDefinition.model_validate(spec)
    )
    assert [c["name"] for c in plan.schema.columns] == ["pv", "zone"]
    compiled = ClickHouseCompiler("semantic").compile(plan)
    sql = compiled.sql
    assert any(
        column.table == "__semantic_pivot_input" and column.name == "pv"
        for column in compiled.query.find_all(exp.Column)
    )
    assert "GROUP BY" not in sql.upper()


def test_omitted_mode_preserves_legacy_serialization_and_sql():
    payload = ManagedDefinition.model_validate(SPEC)
    assert "mode" not in payload.model_dump()["pipeline"][0]
    plan = ManagedTablePlanner().plan(SOURCE, payload)
    assert "mode" not in plan.definition["pipeline"][0]
    compiled = ClickHouseCompiler("semantic").compile(plan)
    assert compiled.compiler_version == "managed-v1-sqlglot-27.28.1"
    assert [c.code for c in compiled.checks] == ["duplicate_pivot_cell"]
    assert "groupArrayIf" in compiled.sql
    spec = deepcopy(SPEC)
    spec["pipeline"][0]["mode"] = "merge_rows"
    explicit = ManagedTablePlanner().plan(
        SOURCE, ManagedDefinition.model_validate(spec)
    )
    assert ClickHouseCompiler("semantic").compile(explicit).sql == compiled.sql
    spec["pipeline"][0]["groupByColumnIds"] = []
    with pytest.raises(MetadataError, match="at least one"):
        ManagedTablePlanner().plan(SOURCE, ManagedDefinition.model_validate(spec))
    spec["pipeline"][0]["mode"] = "unknown"
    with pytest.raises(ValidationError):
        ManagedDefinition.model_validate(spec)


def test_schema_endpoint_derives_ids_before_save_without_runs_or_definition_writes(
    client,
):
    client.post("/api/v1/workspace/bootstrap")
    base = "/api/v1/models/default_model/revisions/revision_1"
    source = row_source()
    assert client.post(base + "/sources", json=source).status_code == 201
    before = client.get(base).json()["version"]
    response = client.post(base + "/managed-tables/raw/schema", json=row_spec())
    assert response.status_code == 200, response.text
    columns = response.json()["columns"]
    assert [c["name"] for c in columns] == ["week", "qty", "brand", "zone"]
    assert all(c["columnId"].startswith("pv_") for c in columns[2:])
    assert client.get(base).json()["version"] == before
    assert client.get(base + "/managed-tables").json()["items"] == []
    assert client.get(base + "/managed-tables/raw").status_code == 404
