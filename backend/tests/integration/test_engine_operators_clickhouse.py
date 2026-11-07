# ruff: noqa: F811
"""Policy invariants executed against ClickHouse, independent of API fixtures."""

import pytest

from backend.app.engine.aggregation import AggregationOperator, AggregationPlan
from backend.app.engine.measure_compiler import MeasureCompiler
from .test_csv_clickhouse import clickhouse_app  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "order, expected", [("nonTimeThenTime", 36), ("timeThenNonTime", 21)]
)
def test_axis_order_preserves_unequal_group_averages(clickhouse_app, order, expected):
    _, warehouse, _, _ = clickhouse_app
    source = "SELECT * FROM values('day UInt8, location String, __value Nullable(Float64), __complete UInt8', (1,'x',2,1),(1,'y',10,1),(2,'x',30,1))"
    policy = {
        "order": order,
        "time": "sum",
        "nonTime": "avg",
        "nullInputPolicy": "ignore",
        "allNullResult": "null",
    }
    result = AggregationOperator.compile(
        MeasureCompiler.__new__(MeasureCompiler),
        source,
        AggregationPlan((), ("day",), ("location",), True, True, policy),
    )
    assert warehouse.query(result.sql).result_rows == [(expected, 1)]


@pytest.mark.parametrize(
    "method,values,expected",
    [
        ("single_value", "'Healthy','At Risk'", (None, 2)),
        ("single_value", "'Healthy','Healthy'", ("Healthy", 1)),
        ("any_true", "true,false", (1, 1)),
        ("all_true", "true,false", (0, 1)),
    ],
)
def test_typed_aggregation_preserves_mixed_and_boolean_states(
    clickhouse_app, method, values, expected
):
    _, warehouse, _, _ = clickhouse_app
    source = f"SELECT arrayJoin([{values}]) AS __value, toUInt8(1) AS __complete"
    policy = {
        "time": method,
        "nonTime": method,
        "nullInputPolicy": "ignore",
        "allNullResult": "null",
    }
    result = AggregationOperator.compile(
        MeasureCompiler.__new__(MeasureCompiler),
        source,
        AggregationPlan((), (), (), False, True, policy),
    )
    assert warehouse.query(result.sql).result_rows == [expected]
