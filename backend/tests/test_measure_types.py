import pytest

from backend.app.services.measure_types import referenced_result_type
from .conftest import ROOT
from .test_metadata_api import definition


@pytest.mark.parametrize(
    "physical_type",
    [
        "Int64",
        "UInt32",
        "Float32",
        "Decimal(18, 3)",
        "LowCardinality(Nullable(Decimal64(4)))",
        "Decimal32(2)",
        "Decimal128(6)",
        "Decimal256(8)",
    ],
)
def test_source_numeric_type_mapping(physical_type):
    assert referenced_result_type(physical_type, False) == {
        "type": "number",
        "nullable": False,
    }


def test_string_is_not_a_measure_value():
    assert referenced_result_type("String", False) is None


def test_referenced_type_is_derived_on_create_and_update(populated):
    payload = definition("measure_fqty")
    payload["objectId"] = "measure_derived"
    payload["definition"]["resultType"] = {"type": "integer", "nullable": False}
    response = populated.post(ROOT + "/measures", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()["definition"]["resultType"]
    assert result["type"] == "number" and result["nullable"] is True
    assert result.get("precision") is None and result.get("scale") is None
    payload["definition"]["resultType"] = {"type": "number", "nullable": False}
    response = populated.put(
        ROOT + "/measures/measure_derived", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 200, response.text
    assert response.json()["definition"]["resultType"] == result


@pytest.mark.parametrize(
    "physical_type", ["Decimal(18,39)", "Decimal32(12)", "Decimal(0,0)"]
)
def test_invalid_source_decimal_parameters_are_rejected(physical_type):
    from backend.app.core.errors import MetadataError

    with pytest.raises(MetadataError, match="invalid decimal"):
        referenced_result_type(physical_type, False)


def test_api_clients_can_omit_referenced_result_type(populated):
    payload = definition("measure_fqty")
    payload["objectId"] = "default_float"
    del payload["definition"]["resultType"]
    response = populated.post(ROOT + "/measures", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["resultType"]["type"] == "number"


def test_legacy_definitions_are_interpreted_without_rewriting_stored_records():
    from backend.app.services.measure_types import normalize_measure

    original = definition("measure_fqty")
    original["definition"]["resultType"] = {"type": "integer", "nullable": True}
    normalized = normalize_measure(original)
    assert normalized["definition"]["resultType"] == {
        "type": "number",
        "nullable": True,
    }
    assert original["definition"]["resultType"]["type"] == "integer"


@pytest.mark.parametrize("policy", [None, "reject", "preserve"])
def test_source_null_handling_is_always_preserve_on_create_and_update(
    populated, policy
):
    payload = definition("measure_fqty")
    payload["objectId"] = "null_handling"
    if policy is None:
        payload["definition"].pop("sourceNullPolicy", None)
    else:
        payload["definition"]["sourceNullPolicy"] = policy
    created = populated.post(ROOT + "/measures", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["definition"]["sourceNullPolicy"] == "preserve"
    payload["definition"]["sourceNullPolicy"] = "reject"
    updated = populated.put(
        ROOT + "/measures/null_handling", json=payload, headers={"If-Match": "1"}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["definition"]["sourceNullPolicy"] == "preserve"


def test_existing_reject_metadata_is_read_as_preserve_without_mutation():
    from backend.app.services.measure_types import normalize_measure

    original = definition("measure_fqty")
    original["definition"]["sourceNullPolicy"] = "reject"
    assert normalize_measure(original)["definition"]["sourceNullPolicy"] == "preserve"
    assert original["definition"]["sourceNullPolicy"] == "reject"
