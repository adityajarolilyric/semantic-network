from copy import deepcopy
import pytest
from backend.app.schemas.formulas import Formula
from backend.app.domain.formula_types import infer
from backend.app.core.errors import MetadataError
from .conftest import ROOT
from .test_metadata_api import definition


def literal(value):
    return {"type": "literal", "value": value}


def call(function, *args):
    return {"type": "call", "function": function, "arguments": list(args)}


@pytest.mark.parametrize(
    "expression,kind",
    [
        (literal("Healthy"), "string"),
        (literal(True), "boolean"),
        (literal(1), "number"),
        (call("date", literal("2026-11-05")), "date"),
        (call("if", literal(True), literal("At Risk"), literal("Healthy")), "string"),
        (call("add_days", call("date", literal("2026-11-05")), literal(2)), "date"),
        (
            call(
                "days_between",
                call("date", literal("2026-11-05")),
                call("date", literal("2026-11-06")),
            ),
            "number",
        ),
    ],
)
def test_typed_expression_inference(expression, kind):
    parsed = Formula(displayText="typed", expression=expression).expression.model_dump()
    assert infer(parsed, lambda _: "number", lambda _: "string") == kind


@pytest.mark.parametrize(
    "expression",
    [
        call("if", literal(1), literal("A"), literal("B")),
        call("if", literal(True), literal(1), literal("B")),
        call("upper", literal(1)),
        call("date", literal("not-a-date")),
        {
            "type": "binary",
            "operator": "add",
            "left": literal("A"),
            "right": literal(1),
        },
        {"type": "binary", "operator": "gt", "left": literal("A"), "right": literal(1)},
    ],
)
def test_invalid_types_fail_before_execution(expression):
    with pytest.raises(MetadataError):
        infer(expression, lambda _: "number", lambda _: "string")


def test_typed_creation_policy_validation_and_attribute_catalog(populated):
    catalog = populated.get(ROOT + "/measures/formula/attributes?dimensionId=pl_plt")
    assert catalog.status_code == 200, catalog.text
    assert any(
        attribute["reference"].get("memberCatalogId") == "dim_product"
        for attribute in catalog.json()
    )
    measure = definition("measure_error")
    measure["objectId"] = "status"
    measure["definition"]["formula"] = {
        "displayText": '"Healthy"',
        "expression": literal("Healthy"),
    }
    response = populated.post(ROOT + "/measures", json=measure)
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["resultType"]["type"] == "string"
    assert response.json()["definition"].get("allocation") is None
    invalid = deepcopy(measure)
    invalid["objectId"] = "bad_status"
    invalid["definition"]["calculationType"] = "CalculateFirst"
    invalid["definition"]["aggregation"] = {"nonTime": "sum", "time": "sum"}
    before = populated.get(ROOT).json()
    response = populated.post(ROOT + "/measures", json=invalid)
    assert response.status_code == 422
    assert populated.get(ROOT).json() == before
    assert populated.get(ROOT + "/measures/bad_status").status_code == 404


def test_direct_attribute_binding_is_required(populated):
    measure = definition("measure_error")
    measure["objectId"] = "bad_attribute"
    measure["definition"]["formula"] = {
        "displayText": "ATTR",
        "expression": {
            "type": "attributeRef",
            "memberCatalogId": "dim_product",
            "columnId": "not_present",
        },
    }
    response = populated.post(ROOT + "/measures", json=measure)
    assert response.status_code == 422, response.text


def test_typed_aggregate_first_can_be_recalculated_for_a_finer_formula(populated):
    flag = definition("measure_error")
    flag["objectId"] = "CoarseFlag"
    flag["definition"]["nativeDimensionId"] = "pl_pt"
    flag["definition"]["formula"] = {
        "displayText": "SQTY > 0",
        "expression": {
            "type": "binary",
            "operator": "gt",
            "left": {"type": "measureRef", "measureId": "measure_sqty"},
            "right": literal(0),
        },
    }
    response = populated.post(ROOT + "/measures", json=flag)
    assert response.status_code == 201, response.text
    finer = definition("measure_error")
    finer["objectId"] = "FinerLabel"
    finer["definition"]["formula"] = {
        "displayText": "IF",
        "expression": call(
            "if",
            {"type": "measureRef", "measureId": "CoarseFlag"},
            literal("Yes"),
            literal("No"),
        ),
    }
    response = populated.post(ROOT + "/measures", json=finer)
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["resultType"]["type"] == "string"


def test_legacy_numeric_baseline_remains_compatible_with_typed_extension():
    import asyncio
    from backend.app.services.incremental_builds import IncrementalBuildCoordinator
    from backend.app.engine.measures.contracts import LEGACY_MEASURE_COMPILER_VERSION
    from backend.app.engine.execution.builds.incremental import INCREMENTAL_POLICY
    from backend.app.domain.measure_policies import MEASURE_TYPE_POLICY
    from backend.app.domain.typed_values import uses_typed_features

    artifact = {
        "compilerVersion": LEGACY_MEASURE_COMPILER_VERSION,
        "numericPolicy": MEASURE_TYPE_POLICY,
        "materialisationManifest": {"policy": INCREMENTAL_POLICY},
    }
    assert (
        asyncio.run(IncrementalBuildCoordinator(None).baseline(None, artifact))
        == artifact["materialisationManifest"]
    )
    assert not uses_typed_features(definition("measure_fqty"))
    typed = definition("measure_error")
    typed["definition"]["formula"]["expression"] = call(
        "days_between",
        call("date", literal("2026-01-01")),
        call("date", literal("2026-01-02")),
    )
    assert uses_typed_features(typed)
