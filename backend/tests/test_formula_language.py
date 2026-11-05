import pytest

from backend.app.formula_language.service import FormulaLanguage
from backend.app.formula_language.parser import formula_parser
from backend.app.core.errors import MetadataError
from .conftest import ROOT
from .test_metadata_api import definition


@pytest.mark.parametrize(
    "source",
    [
        "1 + 2 * 3",
        "COUNT_ROWS()",
        "COUNT_IF([Qty] >= 0)",
        'IF(TRUE, "ok", "no")',
        "ABS(-2.5)",
        'CONCAT("é", "世界")',
    ],
)
def test_lark_syntax(source):
    assert formula_parser().parse(source)


@pytest.mark.parametrize("source", ["SUM(1); DROP TABLE t", "COUNT_IF(", "[Qty", "1 +"])
def test_lark_rejects_sql_and_incomplete_source(source):
    with pytest.raises(MetadataError) as error:
        formula_parser().parse(source)
    assert error.value.details[0]["line"] == 1


def test_backend_language_resolves_and_saves_authoritative_ast(populated):
    body = definition("measure_error")
    body["objectId"] = "language_formula"
    body["name"] = "Language Formula"
    body["definition"]["formula"] = {
        "displayText": "ABS([FQTY] - [SQTY])",
        "languageVersion": "planning-v1",
        "expression": {"type": "literal", "value": 999},
    }
    response = populated.post(ROOT + "/measures", json=body)
    assert response.status_code == 201, response.text
    formula = response.json()["definition"]["formula"]
    assert formula["astVersion"] == 2
    assert formula["expression"]["function"] == "abs"
    assert formula["expression"]["arguments"][0]["type"] == "binary"


def test_validate_counts_uses_output_level_and_is_read_only(populated):
    request = {
        "objectId": "count_test",
        "name": "Count Test",
        "displayText": "COUNT_IF([FQTY] > 0)",
        "nativePlanningLevelId": "pl_pt",
    }
    response = populated.post(ROOT + "/measures/formula/validate", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["containsCounts"]
    assert response.json()["valid"], response.text
    assert response.json()["resultType"]["type"] == "number"
    assert populated.get(ROOT + "/measures/count_test").status_code == 404
    request["displayText"] = "COUNT_IF([FQTY])"
    assert not populated.post(ROOT + "/measures/formula/validate", json=request).json()[
        "valid"
    ]
    request["displayText"] = "COUNT(COUNT_ROWS())"
    assert not populated.post(ROOT + "/measures/formula/validate", json=request).json()[
        "valid"
    ]


def test_function_discovery(populated):
    response = populated.get(ROOT + "/measures/formula/language")
    assert response.status_code == 200
    assert response.json() == FormulaLanguage.catalog()


def test_counts_reject_separate_counting_level_and_calculate_first(populated):
    payload = {
        "objectId": "count",
        "displayText": "COUNT_ROWS()",
        "nativePlanningLevelId": "pl_pt",
    }
    assert populated.post(ROOT + "/measures/formula/validate", json=payload).json()[
        "valid"
    ]
    payload["countingPlanningLevelId"] = "pl_plt"
    assert (
        populated.post(ROOT + "/measures/formula/validate", json=payload).status_code
        == 422
    )
    payload.pop("countingPlanningLevelId")
    payload["calculationType"] = "CalculateFirst"
    assert not populated.post(ROOT + "/measures/formula/validate", json=payload).json()[
        "valid"
    ]


@pytest.mark.parametrize("text", ['"\\q"', "1e9999"])
def test_bad_literals_are_domain_errors(text):
    with pytest.raises(MetadataError):
        formula_parser().parse(text)


def test_count_attributes_resolve_at_output_level(populated):
    payload = {
        "objectId": "count_attr",
        "displayText": "COUNT_DISTINCT(GRAIN([product_id]))",
        "nativePlanningLevelId": "pl_plt",
    }
    result = populated.post(ROOT + "/measures/formula/validate", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["valid"], result.text
    ref = result.json()["expression"]["arguments"][0]
    assert ref["type"] == "attributeRef"
    assert ref["planningLevelId"] == "pl_plt"


def test_validation_detects_indirect_formula_cycle(populated):
    source = definition("measure_fqty")
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": source["objectId"],
            "name": source["name"],
            "displayText": "[measure_error] + 1",
            "nativePlanningLevelId": source["definition"]["nativePlanningLevelId"],
        },
    )
    assert response.status_code == 200, response.text
    assert not response.json()["valid"]
    assert any("cycle" in d["code"] for d in response.json()["diagnostics"])


@pytest.mark.parametrize(
    "text",
    [
        "[Product.product_id]",
        "[Product.Product ID]",
        "[PT.product_id]",
        "[PLT.product_id]",
    ],
)
def test_qualified_column_references_share_one_syntax(populated, text):
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "qualified",
            "displayText": text,
            "nativePlanningLevelId": "pl_plt",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"], response.text
    assert response.json()["expression"]["type"] == "attributeRef"


def test_qualified_reference_still_requires_grain_path(populated):
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "qualified",
            "displayText": "[Location.location_id]",
            "nativePlanningLevelId": "pl_pt",
        },
    )
    assert response.status_code == 200, response.text
    assert not response.json()["valid"]
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "qualified",
            "displayText": "COUNT_DISTINCT([PLT.location_id])",
            "nativePlanningLevelId": "pl_plt",
        },
    )
    assert response.json()["valid"], response.text


def test_qualified_names_do_not_bypass_transaction_measures(populated):
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "qualified",
            "displayText": "[Forecast.forecast_qty]",
            "nativePlanningLevelId": "pl_plt",
        },
    )
    assert not response.json()["valid"]


def test_legacy_reference_functions_are_not_offered(populated):
    functions = populated.get(ROOT + "/measures/formula/language").json()["functions"]
    assert not {"attr", "grain"} & {item["name"] for item in functions}


def test_qualified_reference_ambiguity_is_rejected(populated):
    body = definition("measure_error")
    body["objectId"] = "collision"
    body["name"] = "Product.product_id"
    response = populated.post(ROOT + "/measures", json=body)
    assert response.status_code == 201, response.text
    result = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "qualified",
            "displayText": "[Product.product_id]",
            "nativePlanningLevelId": "pl_plt",
        },
    ).json()
    assert not result["valid"]
    result = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "qualified",
            "displayText": "[dim_product.product_id]",
            "nativePlanningLevelId": "pl_plt",
        },
    ).json()
    assert result["valid"]
    assert result["expression"]["dimensionId"] == "dim_product"


def test_saved_count_levels_normalize_without_mutating_history(populated):
    from copy import deepcopy
    from backend.app.domain.measure_policies import normalize_measure

    old = definition("measure_error")
    old["definition"]["countingPlanningLevelId"] = "pl_plt"
    old["definition"]["nativePlanningLevelId"] = "pl_pt"
    old["definition"]["formula"] = {
        "displayText": 'IF(GRAIN([product_id]) = "A", COUNT_ROWS(), COUNT_ROWS())',
        "languageVersion": "planning-v1",
        "astVersion": 2,
        "expression": {
            "type": "call",
            "function": "if",
            "arguments": [
                {
                    "type": "binary",
                    "operator": "eq",
                    "left": {"type": "grainRef", "grainColumnId": "pt_product"},
                    "right": {"type": "literal", "value": "A"},
                },
                {"type": "count", "function": "count_rows", "arguments": []},
                {"type": "count", "function": "count_rows", "arguments": []},
            ],
        },
    }
    before = deepcopy(old)
    updated = normalize_measure(old)
    assert old == before
    assert updated["definition"]["nativePlanningLevelId"] == "pl_plt"
    assert "countingPlanningLevelId" not in updated["definition"]
    expr = updated["definition"]["formula"]["expression"]
    assert expr["arguments"][0]["left"] == {
        "type": "attributeRef",
        "planningLevelId": "pl_pt",
        "columnId": "pt_product",
    }
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "legacy",
            "nativePlanningLevelId": "pl_plt",
            "displayText": updated["definition"]["formula"]["displayText"],
        },
    )
    assert response.json()["valid"], response.text


def test_unavailable_formula_measures_use_names_instead_of_ids(populated):
    level = {
        "objectId": "independent_qty",
        "name": "Independent Forecast Grain",
        "displayName": "Independent Forecast Grain",
        "definition": {
            "population": {"operation": "source", "sourceId": "src_forecast"},
            "grain": {
                "keyIds": ["quantity"],
                "columns": [
                    {
                        "grainColumnId": "quantity",
                        "name": "quantity",
                        "populationRef": {"columnId": "forecast_qty"},
                    }
                ],
            },
            "dimensionBindings": [],
        },
    }
    response = populated.post(ROOT + "/planning-levels", json=level)
    assert response.status_code == 201, response.text
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "unavailable_formula",
            "displayText": "[FQTY] + [SQTY]",
            "nativePlanningLevelId": "independent_qty",
        },
    )
    assert response.status_code == 200, response.text
    errors = [
        item
        for item in response.json()["diagnostics"]
        if item["code"] == "measure_unavailable"
    ]
    assert len(errors) == 2, response.text
    assert any('"FQTY"' in item["message"] for item in errors)
    assert any('"SQTY"' in item["message"] for item in errors)
    assert all("Independent Forecast Grain" in item["message"] for item in errors)
    assert all(
        "measure_fqty" not in item["message"] and "measure_sqty" not in item["message"]
        for item in errors
    )
