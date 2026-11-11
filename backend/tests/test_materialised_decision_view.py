"""Decision reads choose one semantic source and share its aggregation work."""

from copy import deepcopy

import pytest
from sqlglot import exp

from backend.app.core.errors import MetadataError
from backend.app.engine.decision.compiler import DecisionViewCompiler
from backend.app.engine.decision.planner import DecisionViewPlanner
from backend.app.schemas.decision_view import DecisionViewRequest
from backend.app.engine.decision.cell_states import decode_cell_states
from backend.app.engine.sql.contracts import RelationSchema
from backend.app.warehouse.query_execution import WarehouseQueryExecutor


POLICY = {
    "nonTime": "sum",
    "time": "sum",
    "order": "nonTimeThenTime",
    "nullInputPolicy": "ignore",
    "allNullResult": "null",
}


def level(oid, keys, parent=None):
    return {
        "objectId": oid,
        "kind": "dimension",
        "name": oid,
        "definition": {
            "grain": {
                "keyIds": keys,
                "columns": [
                    {
                        "grainColumnId": key,
                        "name": key,
                        "populationRef": {
                            "sourceGrainColumnId" if parent else "columnId": key
                        },
                    }
                    for key in keys
                ],
            },
            "population": {
                "operation": "distinct_projection",
                "sourceDimensionId": parent,
            }
            if parent
            else {"operation": "source", "sourceId": oid + "_source"},
            "memberBindings": [],
            "timeColumnId": "week" if "week" in keys else None,
        },
    }


def measure(
    mid, native="base", policy=None, formula=None, calculation="AggregateFirst"
):
    return {
        "objectId": mid,
        "kind": "measure",
        "name": {"SalesQty": "Sales Qty", "OtherQty": "Other Qty"}.get(mid, mid),
        "definition": {
            "measureType": "calculated" if formula else "referenced",
            "nativeGrainId": native,
            "aggregation": dict(policy or POLICY),
            "resultType": {"type": "number", "nullable": True},
            **(
                {"calculationType": calculation, "formula": {"expression": formula}}
                if formula
                else {}
            ),
        },
    }


def artifact(lid, keys, measures, rows=6):
    return {
        "artifactId": lid + "_artifact",
        "generationId": "built",
        "storageMode": "current",
        "physicalName": lid + "_table",
        "viewName": lid + "_view",
        "rowCount": rows,
        "keyColumns": {key: key for key in keys},
        "measureColumns": {mid: mid for mid in measures},
        "columns": [{"columnId": key, "name": key, "nullable": False} for key in keys]
        + [
            {
                "columnId": "value_" + mid,
                "name": {"SalesQty": "Sales Qty", "OtherQty": "Other Qty"}.get(
                    mid, mid
                ),
                "measureId": mid,
                "nullable": True,
            }
            for mid in measures
        ],
    }


def example(policy=None):
    keys = ["product", "location", "week"]
    objects = [
        level("base", keys),
        measure("SalesQty", policy=policy),
        measure("Revenue", policy=policy),
    ]
    values = {"base": artifact("base", keys, ["SalesQty", "Revenue"])}
    return objects, values


def request(measures=("SalesQty", "Revenue"), keys=("week",), owner="base"):
    return DecisionViewRequest.model_validate(
        {
            "rows": [{"dimensionId": owner, "columnId": key} for key in keys],
            "measures": list(measures),
        }
    )


def compile_view(objects, values, query=None, database="semantic", base="base"):
    query = query or request()
    plan = DecisionViewPlanner(objects, base).plan(query, values, values)
    compiled = DecisionViewCompiler(
        database, objects, values, {}, base, values, plan
    ).compile(query.measures)
    return plan, compiled


def test_same_source_measures_share_one_scan_and_group_by():
    objects, values = example()
    objects.append(measure("Discount", policy={**POLICY, "nonTime": "avg"}))
    values["base"] = artifact(
        "base", ["product", "location", "week"], ["SalesQty", "Revenue", "Discount"]
    )
    _, compiled = compile_view(
        objects, values, request(["SalesQty", "Revenue", "Discount"])
    )
    assert compiled.compiler_version == "decision-view-materialised-v1"
    assert len(list(compiled.query.find_all(exp.Table))) == 1
    assert len(list(compiled.query.find_all(exp.Group))) == 1
    assert not list(compiled.query.find_all(exp.Join))
    assert "avgornull(" in compiled.sql.lower()
    assert "sumornull(" in compiled.sql.lower()
    assert "__complete" not in compiled.sql


def test_scalar_aggregate_first_dependency_chain_reuses_grouped_columns():
    objects, values = example()
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
    _, compiled = compile_view(objects, values, request(["DoublePrice", "SalesQty"]))
    assert compiled.compiler_version == "decision-view-materialised-v1"
    assert not list(compiled.query.find_all(exp.Join))
    assert len(list(compiled.query.find_all(exp.Table))) == 1
    assert list(compiled.query.find_all(exp.Nullif))


def test_axis_aggregation_order_and_averages_are_not_flattened():
    objects, values = example({**POLICY, "nonTime": "avg", "time": "sum"})
    _, compiled = compile_view(objects, values, request(keys=()))
    assert (
        len(list(compiled.query.find_all(exp.Group))) == 1
    )  # first stage; final stage is global
    assert compiled.sql.lower().count("avgornull(") == 2
    assert compiled.sql.lower().count("sumornull(") == 2
    assert len(list(compiled.query.find_all(exp.Table))) == 1


def test_associative_two_axis_reducers_collapse_safely():
    objects, values = example()
    _, compiled = compile_view(objects, values, request(keys=()))
    assert compiled.sql.lower().count("sumornull(") == 2
    assert not list(compiled.query.find_all(exp.Group))


def test_selects_complete_coarser_source_and_falls_back_to_base():
    objects, values = example()
    objects.append(level("weeks", ["week"], "base"))
    values["weeks"] = artifact("weeks", ["week"], ["SalesQty", "Revenue"], 2)
    plan, compiled = compile_view(objects, values)
    assert plan.level_id == "weeks"
    assert not list(compiled.query.find_all(exp.Group))
    values["weeks"] = artifact("weeks", ["week"], ["Revenue"], 2)
    plan, _ = compile_view(objects, values)
    assert plan.level_id == "base"


def test_related_but_split_representations_require_build_not_a_read_join():
    objects, values = example()
    objects.append(level("weeks", ["week"], "base"))
    values["weeks"] = artifact("weeks", ["week"], ["Revenue"], 2)
    values["base"] = artifact("base", ["product", "location", "week"], ["SalesQty"])
    with pytest.raises(MetadataError) as caught:
        compile_view(objects, values)
    assert caught.value.code == "decision_view_build_required"
    assert caught.value.status == 409
    assert '"Sales Qty"' in caught.value.message
    assert '"Revenue"' in caught.value.message


def test_unrelated_measures_cannot_be_combined_even_for_a_global_total():
    objects, values = example()
    objects.extend(
        [level("independent", ["sku"]), measure("OtherQty", native="independent")]
    )
    values["independent"] = artifact("independent", ["sku"], ["OtherQty"])
    with pytest.raises(MetadataError) as caught:
        compile_view(objects, values, request(["SalesQty", "OtherQty"], keys=()))
    assert caught.value.code == "decision_view_incompatible"
    assert '"Other Qty"' in caught.value.message


def test_null_policies_still_drive_shared_aggregations():
    objects, values = example(
        {**POLICY, "nullInputPolicy": "propagate", "allNullResult": "zero"}
    )
    _, compiled = compile_view(objects, values)
    assert "isNull(" in compiled.sql
    assert "COUNT_IF" in compiled.sql or "countIf" in compiled.sql


def test_coverage_uses_general_same_source_compiler_without_cross_grain_recovery():
    objects, values = example()
    values = deepcopy(values)
    values["base"]["coverageColumns"] = {"SalesQty": "qty_complete"}
    values["base"]["columns"].append(
        {"columnId": "qty_complete", "name": "qty_complete", "nullable": False}
    )
    _, compiled = compile_view(objects, values)
    assert compiled.compiler_version == "decision-view-v1"
    assert {table.name for table in compiled.query.find_all(exp.Table) if table.db} == {
        "base_view"
    }


def test_float64_reads_do_not_recast_and_validate_aggregates_once():
    objects, values = example()
    _, compiled = compile_view(objects, values)
    sql = compiled.sql.lower()
    assert "tofloat64(" not in sql
    assert sql.count("isfinite(") == 2  # one final check per aggregated output
    assert "countif(" not in sql  # OrNull supplies all-null semantics
    _, exact = compile_view(
        objects, values, request(keys=("product", "location", "week"))
    )
    assert "tofloat64(" not in exact.sql.lower()
    assert "throwIf" not in exact.sql  # stored values were validated on build


def test_calculated_numeric_result_is_converted_once():
    objects, values = example()
    objects.append(measure("Constant", formula={"type": "literal", "value": 7}))
    _, compiled = compile_view(objects, values, request(["Constant", "SalesQty"]))
    assert compiled.sql.lower().count("tofloat64(") == 1
    assert compiled.sql.lower().count("isfinite(") == 2


def test_nonnullable_aggregates_keep_the_final_null_guard():
    objects, values = example()
    objects[1]["definition"]["resultType"]["nullable"] = False
    _, compiled = compile_view(objects, values)
    assert compiled.sql.count("SEMANTIC_MEASURE_NULL") == 1
    assert compiled.sql.count("SEMANTIC_MEASURE_OVERFLOW") == 1


def test_full_precision_and_constant_states_are_not_projected_in_sql():
    _, compiled = compile_view(*example())
    assert "roundBankers" not in compiled.sql
    assert "__mixed" not in compiled.sql
    assert compiled.schema.columns == ()
    assert decode_cell_states(
        [["W1", 1.123456789, None]], ["SalesQty", "Revenue"], compiled.schema
    ) == [[None, None]]


def test_dynamic_states_decode_into_the_original_measure_positions():
    schema = RelationSchema(
        (
            {"role": "mixed", "measureId": "Status", "resultIndex": 4},
            {"role": "mixed", "measureId": "CountStatus", "resultIndex": 5},
        )
    )
    assert decode_cell_states(
        [["P1", None, 10, None, 1, 1], ["P2", "OK", 20, 1, 0, 0]],
        ["Status", "Qty", "CountStatus"],
        schema,
    ) == [["mixed", None, "mixed"], [None, None, None]]


def test_projection_only_input_merges_into_the_grouped_table_read():
    _, compiled = compile_view(*example())
    grouped = next(
        select
        for select in compiled.query.find_all(exp.Select)
        if select.args.get("group")
    )
    assert isinstance(grouped.args["from"].this, exp.Table)
    assert len(list(compiled.query.find_all(exp.Select))) == 2
    assert not any(
        isinstance(item, exp.Alias) for item in grouped.args["group"].expressions
    )
    bounded = WarehouseQueryExecutor.decision_view_sql(compiled, 50)
    assert not bounded.startswith("SELECT *")
    assert bounded.endswith("ORDER BY ALL LIMIT 51")
    assert compiled.query.args.get("limit") is None


def test_selected_shared_formula_does_not_expand_its_dependency_dag():
    objects, values = example()
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
    _, compiled = compile_view(objects, values, request(["Price", "DoublePrice"]))
    assert compiled.sql.lower().count("nullif(") == 1
