"""Execute shared materialised reads against the established semantic compiler."""

# ruff: noqa: F811

import pytest
from clickhouse_connect.driver.exceptions import DatabaseError

from backend.app.engine.decision import materialised
from backend.app.engine.decision.compiler import DecisionViewCompiler
from backend.app.engine.measures.aggregation import aggregate_expression
from backend.app.engine.decision.materialised import MaterialisedViewCompiler
from backend.app.engine.decision.planner import DecisionViewPlanner
from backend.tests.test_materialised_decision_view import (
    POLICY,
    artifact,
    compile_view,
    example,
    measure,
    request,
)
from .test_csv_clickhouse import clickhouse_app  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "non_time,time_method,order,nulls,empty",
    [
        ("sum", "sum", "nonTimeThenTime", "ignore", "null"),
        ("avg", "sum", "nonTimeThenTime", "ignore", "null"),
        ("avg", "sum", "timeThenNonTime", "ignore", "null"),
        ("avg", "avg", "nonTimeThenTime", "ignore", "null"),
        ("min", "min", "nonTimeThenTime", "ignore", "zero"),
        ("max", "max", "nonTimeThenTime", "propagate", "null"),
        ("sum", "sum", "nonTimeThenTime", "propagate", "zero"),
    ],
)
def test_shared_read_matches_policy_compiler(
    clickhouse_app, monkeypatch, non_time, time_method, order, nulls, empty
):
    _, admin, database, _ = clickhouse_app
    admin.command(f"""CREATE TABLE `{database}`.base_table (
        product String, location String, week String, `Sales Qty` Nullable(Float64), Revenue Nullable(Float64)
    ) ENGINE=Memory""")
    admin.command(
        f"""CREATE VIEW `{database}`.base_view AS SELECT * FROM `{database}`.base_table"""
    )
    admin.command(f"""INSERT INTO `{database}`.base_table VALUES
        ('P1','L1','W1',10,100), ('P2','L1','W1',20,600), ('P1','L1','W2',30,1200),
        ('P1','L1','W3',NULL,NULL), ('P2','L1','W2',NULL,500)""")
    policy = {
        **POLICY,
        "nonTime": non_time,
        "time": time_method,
        "order": order,
        "nullInputPolicy": nulls,
        "allNullResult": empty,
    }
    objects, values = example(policy)
    objects.extend(
        [
            measure(
                "Price",
                formula={
                    "type": "binary",
                    "operator": "divide",
                    "left": {"type": "measureRef", "measureId": "Revenue"},
                    "right": {"type": "measureRef", "measureId": "SalesQty"},
                },
            ),
            measure(
                "DoublePrice",
                formula={
                    "type": "binary",
                    "operator": "multiply",
                    "left": {"type": "measureRef", "measureId": "Price"},
                    "right": {"type": "literal", "value": 2},
                },
            ),
        ]
    )
    queries = [
        request(["SalesQty", "Revenue", "DoublePrice"], keys=keys)
        for keys in [("week",), (), ("product", "location", "week")]
    ]
    optimized = []
    for query in queries:
        _, compiled = compile_view(objects, values, query, database=database)
        assert compiled.compiler_version == "decision-view-materialised-v1"
        optimized.append((compiled, admin.query(compiled.sql).result_rows))
    monkeypatch.setattr(MaterialisedViewCompiler, "compile", lambda *_: None)
    for query, (shared, actual) in zip(queries, optimized):
        _, general = compile_view(objects, values, query, database=database)
        assert sorted(actual, key=str) == sorted(
            admin.query(general.sql).result_rows, key=str
        )
        assert len(shared.sql) < len(general.sql)


def test_exact_stored_aggregate_first_without_aggregation_policy(clickhouse_app):
    _, admin, database, _ = clickhouse_app
    admin.command(f"""CREATE TABLE `{database}`.base_table (
        product String, location String, week String, Price Nullable(Float64)
    ) ENGINE=Memory""")
    admin.command(
        f"""CREATE VIEW `{database}`.base_view AS SELECT * FROM `{database}`.base_table"""
    )
    admin.command(f"""INSERT INTO `{database}`.base_table VALUES ('P1','L1','W1',5)""")
    objects, _ = example()
    price = measure("Price", formula={"type": "literal", "value": 5})
    price["definition"]["aggregation"] = None
    objects.append(price)
    keys = ["product", "location", "week"]
    values = {"base": artifact("base", keys, ["Price"])}
    query = request(["Price"], keys=keys)
    plan = DecisionViewPlanner(objects, "base").plan(query, values, values)
    assert plan.leaves == {"Price"}
    compiled = DecisionViewCompiler(
        database, objects, values, {}, "base", values, plan
    ).compile(query.measures)
    assert admin.query(compiled.sql).result_rows == [("P1", "L1", "W1", 5)]


def test_shared_source_preserves_typed_mixed_and_count_semantics(clickhouse_app):
    _, admin, database, _ = clickhouse_app
    admin.command(f"""CREATE TABLE `{database}`.base_table (
        product String, location String, week String, `Sales Qty` Nullable(Float64),
        Revenue Nullable(Float64), Status Nullable(String)
    ) ENGINE=Memory""")
    admin.command(
        f"""CREATE VIEW `{database}`.base_view AS SELECT * FROM `{database}`.base_table"""
    )
    admin.command(f"""INSERT INTO `{database}`.base_table VALUES
        ('P1','L1','W1',10,100,'Healthy'), ('P2','L1','W1',20,600,'At Risk'),
        ('P1','L1','W2',30,1200,'Healthy')""")
    objects, _ = example()
    status = measure(
        "Status", policy={**POLICY, "nonTime": "single_value", "time": "single_value"}
    )
    status["definition"]["resultType"] = {"type": "string", "nullable": True}
    objects.extend(
        [
            status,
            measure(
                "Rows",
                formula={"type": "count", "function": "count_rows", "arguments": []},
            ),
        ]
    )
    values = {
        "base": artifact(
            "base", ["product", "location", "week"], ["SalesQty", "Revenue", "Status"]
        )
    }
    query = request(["Status", "SalesQty", "Rows"])
    _, compiled = compile_view(objects, values, query, database=database)
    assert compiled.compiler_version == "decision-view-v1"
    assert sorted(admin.query(compiled.sql).result_rows) == [
        ("W1", None, 30, 2, 1, 0),
        ("W2", "Healthy", 30, 1, 0, 0),
    ]


@pytest.mark.parametrize("empty", ["null", "zero"])
@pytest.mark.parametrize("nulls", ["ignore", "propagate"])
def test_compact_empty_and_all_null_groups_match_previous_shared_aggregates(
    clickhouse_app, monkeypatch, empty, nulls
):
    _, admin, database, _ = clickhouse_app
    admin.command(f"""CREATE TABLE `{database}`.base_table (
        product String, location String, week String, `Sales Qty` Nullable(Float64), Revenue Nullable(Float64)
    ) ENGINE=Memory""")
    admin.command(
        f"""CREATE VIEW `{database}`.base_view AS SELECT * FROM `{database}`.base_table"""
    )
    objects, values = example(
        {
            **POLICY,
            "nonTime": "avg",
            "time": "sum",
            "allNullResult": empty,
            "nullInputPolicy": nulls,
        }
    )
    query = request(keys=())
    _, compact = compile_view(objects, values, query, database=database)
    # Compare the deployed shared path's previous explicit NULL handling. The
    # older general compiler additionally treats an entirely empty population
    # as incomplete, which is a separate coverage contract.
    monkeypatch.setattr(
        materialised,
        "aggregate_expression",
        lambda *args, **kwargs: aggregate_expression(
            *args, **{**kwargs, "compact": False}
        ),
    )
    _, general = compile_view(objects, values, query, database=database)
    for populated in (False, True):
        if populated:
            admin.command(
                f"""INSERT INTO `{database}`.base_table VALUES ('P1','L1','W1',NULL,NULL)"""
            )
        actual = admin.query(compact.sql).result_rows
        assert actual == admin.query(general.sql).result_rows
        expected = (
            [(None, None)]
            if empty == "null" or (populated and nulls == "propagate")
            else [(0, 0)]
        )
        assert actual == expected


def test_compact_float_results_preserve_error_and_precision_contracts(clickhouse_app):
    _, admin, database, _ = clickhouse_app
    admin.command(f"""CREATE TABLE `{database}`.base_table (
        product String, location String, week String, `Sales Qty` Nullable(Float64), Revenue Nullable(Float64)
    ) ENGINE=Memory""")
    admin.command(
        f"""CREATE VIEW `{database}`.base_view AS SELECT * FROM `{database}`.base_table"""
    )
    objects, values = example()
    query = request(["SalesQty"], keys=())
    _, compiled = compile_view(objects, values, query, database=database)
    admin.command(f"""INSERT INTO `{database}`.base_table VALUES
        ('P1','L1','W1',1e308,0), ('P2','L1','W1',1e308,0)""")
    with pytest.raises(DatabaseError, match="SEMANTIC_MEASURE_OVERFLOW"):
        admin.query(compiled.sql)
    admin.command(f"""TRUNCATE TABLE `{database}`.base_table""")
    admin.command(
        f"""INSERT INTO `{database}`.base_table VALUES ('P1','L1','W1',1e20,0)"""
    )
    assert admin.query(compiled.sql).result_rows == [(1e20,)]
    admin.command(f"""TRUNCATE TABLE `{database}`.base_table""")
    admin.command(
        f"""INSERT INTO `{database}`.base_table VALUES ('P1','L1','W1',0.123456789,0)"""
    )
    assert admin.query(compiled.sql).result_rows == [(0.123456789,)]
    objects[1]["definition"]["resultType"]["nullable"] = False
    _, nonnullable = compile_view(objects, values, query, database=database)
    admin.command(f"""TRUNCATE TABLE `{database}`.base_table""")
    with pytest.raises(DatabaseError, match="SEMANTIC_MEASURE_NULL"):
        admin.query(nonnullable.sql)


def test_compact_calculated_result_conversion_and_safe_division(clickhouse_app):
    _, admin, database, _ = clickhouse_app
    admin.command(f"""CREATE TABLE `{database}`.base_table (
        product String, location String, week String, `Sales Qty` Nullable(Float64), Revenue Nullable(Float64)
    ) ENGINE=Memory""")
    admin.command(
        f"""CREATE VIEW `{database}`.base_view AS SELECT * FROM `{database}`.base_table"""
    )
    admin.command(
        f"""INSERT INTO `{database}`.base_table VALUES ('P1','L1','W1',0,100)"""
    )
    objects, values = example()
    objects.extend(
        [
            measure("Constant", formula={"type": "literal", "value": 7}),
            measure(
                "Price",
                formula={
                    "type": "binary",
                    "operator": "divide",
                    "left": {"type": "measureRef", "measureId": "Revenue"},
                    "right": {"type": "measureRef", "measureId": "SalesQty"},
                },
            ),
            measure(
                "Overflow",
                formula={
                    "type": "binary",
                    "operator": "multiply",
                    "left": {"type": "literal", "value": 1e308},
                    "right": {"type": "literal", "value": 2},
                },
            ),
        ]
    )
    _, compiled = compile_view(
        objects, values, request(["Constant", "Price", "SalesQty"]), database=database
    )
    result = admin.query(compiled.sql)
    assert result.result_rows == [("W1", 7, None, 0)]
    assert result.column_types[1].name == "Nullable(Float64)"
    _, overflow = compile_view(
        objects, values, request(["Overflow", "SalesQty"]), database=database
    )
    with pytest.raises(DatabaseError, match="SEMANTIC_MEASURE_OVERFLOW"):
        admin.query(overflow.sql)


def test_projection_merging_preserves_shared_formulas_and_source_aliases(
    clickhouse_app,
):
    _, admin, database, _ = clickhouse_app
    admin.command(f"""CREATE TABLE `{database}`.base_table (
        product String, location String, week String, `Sales Qty` Nullable(Float64), value_0 Nullable(Float64)
    ) ENGINE=Memory""")
    admin.command(
        f"""CREATE VIEW `{database}`.base_view AS SELECT * FROM `{database}`.base_table"""
    )
    admin.command(
        f"""INSERT INTO `{database}`.base_table VALUES ('P1','L1','W1',10,30)"""
    )
    objects, values = example()
    next(
        column
        for column in values["base"]["columns"]
        if column.get("measureId") == "Revenue"
    )["name"] = "value_0"
    objects.extend(
        [
            measure(
                "Price",
                formula={
                    "type": "binary",
                    "operator": "divide",
                    "left": {"type": "measureRef", "measureId": "Revenue"},
                    "right": {"type": "measureRef", "measureId": "SalesQty"},
                },
            ),
            measure(
                "DoublePrice",
                formula={
                    "type": "binary",
                    "operator": "add",
                    "left": {"type": "measureRef", "measureId": "Price"},
                    "right": {"type": "measureRef", "measureId": "Price"},
                },
            ),
        ]
    )
    for keys in [("week",), ("product", "location", "week")]:
        _, compiled = compile_view(
            objects,
            values,
            request(["Price", "DoublePrice"], keys=keys),
            database=database,
        )
        assert compiled.sql.lower().count("nullif(") == 1
        result = admin.query(compiled.sql).result_rows
        assert len(result) == 1
        assert result[0][-2:] == (3, 6)
