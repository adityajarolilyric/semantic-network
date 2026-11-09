"""Compatibility checks for shared query operators and standard-library DAG ordering."""

import pytest
from hypothesis import given, strategies as st
from sqlglot import exp, parse_one

from backend.app.core.errors import Conflict
from backend.app.engine.measures.aggregation import AggregationOperator, AggregationPlan
from backend.app.engine.graph.dependencies import ordered_dimensions
from backend.app.engine.measures.compiler import MeasureCompiler
from backend.app.engine.sql.snapshot_reads import snapshot_read
from backend.app.engine.sql.storage import GENERATION


@given(st.lists(st.integers(min_value=-1, max_value=20), max_size=25))
def test_dimension_order_is_stable_and_respects_dependencies(parents):
    objects = []
    for i, candidate in enumerate(parents):
        parent = candidate % i if i and candidate >= 0 else None
        population = (
            {"operation": "source"}
            if parent is None
            else {"operation": "distinct_projection", "sourceDimensionId": str(parent)}
        )
        objects.append(
            {
                "objectId": str(i),
                "kind": "dimension",
                "definition": {"population": population},
            }
        )
    result = ordered_dimensions(objects)
    assert result == ordered_dimensions(reversed(objects))
    positions = {obj["objectId"]: i for i, obj in enumerate(result)}
    for obj in objects:
        parent = obj["definition"]["population"].get("sourceDimensionId")
        if parent is not None:
            assert positions[parent] < positions[obj["objectId"]]
    # Preserve the existing sorted-ready-batch order, including independent roots.
    remaining = {obj["objectId"]: obj for obj in objects}
    expected = []
    while remaining:
        ready = sorted(
            key
            for key, obj in remaining.items()
            if obj["definition"]["population"].get("sourceDimensionId") not in remaining
        )
        expected.extend(remaining.pop(key) for key in ready)
    assert result == expected


@pytest.mark.parametrize("parent", ["missing", "a"])
def test_dimension_order_rejects_missing_parent_and_cycle(parent):
    with pytest.raises(Conflict):
        ordered_dimensions(
            [
                {
                    "objectId": "a",
                    "kind": "dimension",
                    "definition": {
                        "population": {
                            "operation": "distinct_projection",
                            "sourceDimensionId": parent,
                        }
                    },
                }
            ]
        )


def test_snapshot_read_preserves_quoted_keys_generation_and_value_states():
    artifact = {
        "generationToken": "saved-token",
        "artifactId": "fallback-token",
        "measureColumns": {"m": "sales value"},
        "keyColumns": {"product": "Product ID"},
        "coverageColumns": {"m": "covered"},
        "mixedColumns": {"m": "mixed"},
        "columns": [
            {
                "measureId": "m",
                "name": "sales value",
                "physicalType": "Nullable(Float64)",
                "nullable": True,
                "storageName": "sales value",
            }
        ],
    }
    read = snapshot_read("`warehouse`.`values table`", artifact, "m", ["product"])
    sql = read.query.sql(dialect="clickhouse")
    assert read.coverage and read.mixed
    table = read.query.find(exp.Table)
    assert table.db == "warehouse" and table.name == "values table"
    assert GENERATION in sql and "saved-token" in sql
    assert read.query.expressions[0].alias == "product"
    assert read.query.expressions[0].this.name == "Product ID"
    assert 'coalesce("mixed", 0)' in sql
    assert parse_one(sql, read="clickhouse").sql(dialect="clickhouse") == sql


def aggregate_sql(policy, *, mixed=False, ordering=None):
    plan = AggregationPlan(
        ("target",), ("day",), ("location",), True, True, policy, ordering
    )
    return AggregationOperator.compile(
        MeasureCompiler.__new__(MeasureCompiler),
        "SELECT target, day, location, __value, __complete, day AS __time_order FROM input",
        plan,
        mixed,
    )


@pytest.mark.parametrize("order", ["nonTimeThenTime", "timeThenNonTime"])
@pytest.mark.parametrize(
    "method",
    [
        "sum",
        "avg",
        "min",
        "max",
        "single_value",
        "count",
        "count_distinct",
        "any_true",
        "all_true",
    ],
)
def test_operator_preserves_policy_and_sqlglot_round_trip(order, method):
    result = aggregate_sql(
        {
            "order": order,
            "time": method,
            "nonTime": method,
            "nullInputPolicy": "ignore",
            "allNullResult": "null",
        }
    )
    assert result.has_mixed == (method == "single_value")
    assert (
        parse_one(result.sql, read="clickhouse").sql(dialect="clickhouse") == result.sql
    )


def test_two_axis_order_preserves_intermediate_grain_and_first_last_ordering():
    policy = {
        "time": "last",
        "nonTime": "avg",
        "nullInputPolicy": "propagate",
        "allNullResult": "null",
    }
    plan = AggregationPlan(
        ("target",), ("day",), ("location",), True, True, policy, "day"
    )
    assert plan.stages()[0].group_keys == ("target", "day")
    assert plan.stages()[0].carry_time_order
    assert "argMaxIf" in aggregate_sql(policy, ordering="day").sql
    reverse = AggregationPlan(
        ("target",),
        ("day",),
        ("location",),
        True,
        True,
        {**policy, "order": "timeThenNonTime"},
        True,
    )
    assert reverse.stages()[0].group_keys == ("target", "location")


def test_failed_branch_rollback_keeps_context_aliases_and_clears_input_cache():
    from backend.app.engine.sql.compilation_checkpoint import CompilationCheckpoint
    from .test_incremental import model

    objects, populations, managed = model()
    compiler = MeasureCompiler("warehouse", objects, populations, managed, "base")
    kept = compiler.cte("SELECT 1 AS existing")
    compiler.cache["existing"] = kept
    checkpoint = CompilationCheckpoint.capture(compiler)
    discarded = compiler.cte("SELECT 2 AS abandoned")
    compiler.object_inputs["pivot"] = (discarded, [])
    compiler.assertions.append("abandoned check")
    compiler.cache["abandoned"] = discarded
    compiler.mixed_nodes.add(discarded)
    checkpoint.restore()
    assert compiler.ctes is compiler.context.ctes
    assert compiler.assertions is compiler.context.input_checks
    assert compiler.object_inputs is compiler.context.object_inputs
    assert compiler.object_inputs == {}
    assert compiler.assertions == []
    assert compiler.cache == {"existing": kept}
    assert compiler.ctes == {kept: "SELECT 1 AS existing"}
    assert "abandoned" not in compiler.with_sql("SELECT * FROM " + kept)
    assert compiler.cte("SELECT 3 AS replacement") == discarded
