"""Named authoring uses Lark; persisted expressions remain backward compatible."""

import json

import pytest

from backend.app.core.errors import MetadataError
from backend.app.formula_language.parser import formula_parser
from backend.app.warehouse.sql import quote_identifier
from .conftest import ROOT
from .test_metadata_api import definition


@pytest.mark.parametrize(
    "name",
    [
        "error",
        "Absolute Error",
        "  Absolute Error  ",
        "Écart 世界",
        "2026 Error",
        "if",
        "Error's %",
        "a`b\\c",
        "A  B",
    ],
)
def test_unquoted_named_formula(name):
    parsed = formula_parser().parse_definition(f"{name} = abs(1 - 2)")
    assert parsed.name == name.strip()
    assert parsed.expression_text == "abs(1 - 2)"
    assert parsed.expression["name"] == "abs"


@pytest.mark.parametrize(
    "name", ["A = B", 'A "quoted" name', "[Error]", "a`b\\c", "世界"]
)
def test_quoted_named_formula(name):
    parsed = formula_parser().parse_definition(
        f'  {json.dumps(name)} = IF(1 = 1, "=", "no")'
    )
    assert parsed.name == name
    assert parsed.expression["arguments"][0]["operator"] == "eq"
    assert parsed.expression_text == 'IF(1 = 1, "=", "no")'


def test_bracket_name_and_multiline_expression():
    parsed = formula_parser().parse_definition("[ Absolute Error ] =\n ABS(\n1 - 2\n)")
    assert parsed.name == "Absolute Error"
    assert parsed.expression_text == "ABS(\n1 - 2\n)"
    assert parsed.expression["line"] == 2


@pytest.mark.parametrize(
    "source",
    [
        "= 1",
        "  = 1",
        '"  " = 1',
        "Error =",
        "Error ABS(1)",
        "Error = 1; DROP TABLE t",
        '"a\\nname" = 1',
        '"a\\u0000name" = 1',
        '"a\\ud800name" = 1',
        "a" * 201 + " = 1",
    ],
)
def test_invalid_definitions_are_domain_errors(source):
    with pytest.raises(MetadataError):
        formula_parser().parse_definition(source)


def test_legacy_equality_expression_still_parses():
    assert formula_parser().parse("x = y")["operator"] == "eq"


def named_payload(source=" Absolute Error = abs([FQTY] - [SQTY]) "):
    body = definition("measure_error")
    body["objectId"] = "named_error"
    body.pop("name")
    body.pop("displayName")
    body["formulaDefinition"] = source
    return body


def test_named_api_creation_derives_identity_and_does_not_persist_command(populated):
    body = named_payload()
    # The command is authoritative over stale client-supplied source and AST.
    body["definition"]["formula"] = {
        "displayText": "999",
        "expression": {"type": "literal", "value": 999},
    }
    response = populated.post(ROOT + "/measures", json=body)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["name"] == saved["displayName"] == "Absolute Error"
    assert "formulaDefinition" not in saved
    formula = saved["definition"]["formula"]
    assert formula["displayText"] == "abs([FQTY] - [SQTY])"
    assert formula["expression"]["function"] == "abs"
    assert formula["astVersion"] == 2
    assert (
        "formulaDefinition" not in populated.get(ROOT + "/measures/named_error").json()
    )


def test_named_validation_and_renaming_keep_stable_identity(populated):
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "named_error",
            "requireAssignment": True,
            "displayText": " Absolute Error = abs([FQTY] - [SQTY]) ",
            "nativeDimensionId": "pl_pt",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"], response.text
    assert response.json()["name"] == "Absolute Error"
    assert response.json()["expressionText"] == "abs([FQTY] - [SQTY])"
    response = populated.post(ROOT + "/measures", json=named_payload())
    assert response.status_code == 201, response.text
    response = populated.put(
        ROOT + "/measures/named_error",
        headers={"If-Match": response.headers["etag"]},
        json=named_payload('"Error = Absolute" = ABS([FQTY] - [SQTY])'),
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Error = Absolute"
    assert response.json()["objectId"] == "named_error"


def test_invalid_assignment_rejected_on_validate_and_save(populated):
    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "named_error",
            "requireAssignment": True,
            "displayText": "ABS([FQTY])",
            "nativeDimensionId": "pl_pt",
        },
    )
    assert not response.json()["valid"]
    response = populated.post(ROOT + "/measures", json=named_payload("= 1"))
    assert response.status_code == 422, response.text
    assert populated.get(ROOT + "/measures/named_error").status_code == 404


def test_assignment_overrides_separate_name(populated):
    body = named_payload("FQTY = 1")
    body["name"] = body["displayName"] = "Different name"
    response = populated.post(ROOT + "/measures", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "FQTY"


def test_special_names_are_quoted_as_clickhouse_identifiers():
    assert quote_identifier("Absolute Error") == "`Absolute Error`"
    assert quote_identifier("a`b\\c") == "`a\\`b\\\\c`"


def test_escaped_bracket_references_resolve_named_measure(populated):
    response = populated.post(
        ROOT + "/measures",
        json=named_payload('"Error [absolute]" = ABS([FQTY] - [SQTY])'),
    )
    assert response.status_code == 201, response.text
    payload = named_payload("Double = [Error [absolute]]] * 2")
    payload["objectId"] = "double_error"
    response = populated.post(ROOT + "/measures", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["formula"]["expression"]["left"] == {
        "type": "measureRef",
        "measureId": "named_error",
    }
