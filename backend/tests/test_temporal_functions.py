from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.core.errors import MetadataError
from backend.app.domain.formula_types import infer
from backend.app.engine.execution.builds.incremental import MaterialisationManifest
from backend.app.engine.measures.dependencies import MeasureDependencies
from backend.app.formula_language.service import FormulaLanguage
from backend.app.formula_language.sql import FormulaSqlCompiler
from backend.app.formula_language.temporal import window_spec
from backend.app.schemas.temporal import TemporalContext
from backend.app.schemas.measures import ReferencedMeasureDefinition
from .test_incremental import model


def resolve(text):
    return FormulaLanguage.resolve(text, [], None)


@pytest.mark.parametrize(
    "text,expected",
    [
        ('rolling(1,"sum",-3,0)', ("sum", -3, 0)),
        ('ROLLING(1,"STDDEV_SAMP",0,3)', ("stddevSamp", 0, 3)),
        ('CUMULATIVE(1,"AVG")', ("avg", None, 0)),
        ("lag(1,2)", ("anyOrNull", -2, -2)),
        ("lead(1,2)", ("anyOrNull", 2, 2)),
    ],
)
def test_generic_temporal_operations_and_case_insensitive_functions(text, expected):
    expression = resolve(text)
    assert window_spec(expression) == expected
    assert infer(expression, lambda _: "number", lambda _: "number") == "number"


@pytest.mark.parametrize(
    "text",
    [
        'ROLLING(1,"SUM",3,0)',
        'ROLLING(1,"SQL",-3,0)',
        'ROLLING(1,"SUM",-1.5,0)',
        "LAG(1,0)",
        "LEAD(1,-1)",
        'ROLLING(1,"SUM",-10001,0)',
        'CUMULATIVE("text","SUM")',
    ],
)
def test_invalid_windows_are_semantic_errors(text):
    with pytest.raises(MetadataError):
        infer(resolve(text), lambda _: "number", lambda _: "number")


@pytest.mark.parametrize(
    "text,sql_function",
    [
        ("sqrt(4)", "sqrt"),
        ("root(-8,3)", "pow"),
        ("power(2,3)", "pow"),
        ("ceil(1.2)", "ceil"),
        ("floor(1.9)", "floor"),
    ],
)
def test_math_uses_typed_sqlglot_nodes(text, sql_function):
    expression = resolve(text)
    assert infer(expression, lambda _: "number", lambda _: "number") == "number"
    sql = (
        FormulaSqlCompiler(None, None, None, {})
        .compile(expression)
        .sql(dialect="clickhouse")
    )
    assert sql_function in sql
    assert ";" not in sql


def test_catalog_has_business_groups_without_changing_semantic_categories():
    catalog = FormulaLanguage.catalog()["functions"]
    functions = {item["name"]: item for item in catalog}
    assert functions["abs"]["group"] == "Arithmetic"
    assert functions["if"]["group"] == "Conditional"
    assert functions["and"]["group"] == "Logical"
    assert functions["upper"]["group"] == "Text"
    assert functions["date"]["group"] == "Date"
    assert functions["rolling"]["group"] == "Time series"
    assert functions["count"]["group"] == "Counting"
    assert functions["count"]["category"] == "aggregate"


def test_series_keys_must_not_include_time_key():
    with pytest.raises(ValidationError):
        TemporalContext(
            timeKeyId="week", orderColumnId="ordinal", partitionColumnIds=["week"]
        )


def test_factors_are_read_only_unique_values_and_repeat_at_finer_grains():
    factor = ReferencedMeasureDefinition.model_validate(
        {
            "measureType": "referenced",
            "transactionId": "rates",
            "valueColumnId": "factor",
            "aggregation": {"nonTime": "sum", "time": "sum"},
            "editable": True,
            "isConversionFactor": True,
        }
    )
    assert not factor.editable
    assert factor.allocation.mode == "none"
    assert factor.aggregation.nonTime == factor.aggregation.time == "single_value"


def test_conversion_dependencies_are_fingerprinted_and_sorted_before_consumers():
    objects, populations, managed = model()
    qty = next(item for item in objects if item["objectId"] == "FQTY")
    qty["definition"]["conversion"] = {
        "factorMeasureId": "SQTY",
        "targetUnit": "EA",
        "operation": "multiply",
    }
    graph = MeasureDependencies(objects)
    assert graph.ordered("FQTY") == ("SQTY", "FQTY")
    first = MaterialisationManifest(objects, populations, managed, "base").evaluation(
        "FQTY", "base"
    )
    changed_managed = deepcopy(managed)
    changed_managed["sales"]["artifactId"] = "new_rates"
    second = MaterialisationManifest(
        objects, populations, changed_managed, "base"
    ).evaluation("FQTY", "base")
    assert first != second


def test_new_optional_defaults_preserve_existing_numeric_fingerprints():
    objects, populations, managed = model()
    first = MaterialisationManifest(objects, populations, managed, "base").all()
    for item in objects:
        if item["kind"] == "measure":
            item["definition"].update(
                temporal=None, conversion=None, isConversionFactor=False
            )
    second = MaterialisationManifest(objects, populations, managed, "base").all()
    assert first["base"]["evaluations"] == second["base"]["evaluations"]


def test_saved_ast_wrong_window_arity_is_a_domain_error():
    with pytest.raises(MetadataError, match="argument count"):
        infer(
            {
                "type": "call",
                "function": "rolling",
                "arguments": [{"type": "literal", "value": 1}],
            },
            lambda _: "number",
            lambda _: "number",
        )


def test_factor_use_excludes_count_arguments_but_includes_arithmetic():
    from backend.app.formula_language.expressions import conversion_factor_references

    objects = {"rate": {"definition": {"isConversionFactor": True}}}
    reference = {"type": "measureRef", "measureId": "rate"}
    assert conversion_factor_references(reference, objects) == ("rate",)
    assert (
        conversion_factor_references(
            {"type": "count", "function": "count", "arguments": [reference]}, objects
        )
        == ()
    )
