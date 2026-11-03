import pytest

from .conftest import ROOT
from .test_metadata_api import definition


def test_weighted_by_self_saves_without_a_circular_measure_reference(populated):
    payload = definition("measure_sqty")
    payload["definition"]["allocation"] = {"mode": "weighted_by_self"}
    response = populated.put(
        ROOT + "/measures/measure_sqty", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 200, response.text
    assert response.json()["definition"]["allocation"] == {
        "mode": "weighted_by_self",
        "basisMeasureId": None,
    }
    assert populated.post(ROOT + "/validate").json()["valid"] is True


@pytest.mark.parametrize(
    "mode,basis",
    [
        ("weighted_by_self", "measure_sqty"),
        ("equal", "measure_sqty"),
        ("weighted", None),
    ],
)
def test_only_separate_weighted_policy_accepts_a_basis(populated, mode, basis):
    payload = definition("measure_sqty")
    payload["definition"]["allocation"] = {"mode": mode, "basisMeasureId": basis}
    response = populated.put(
        ROOT + "/measures/measure_sqty", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("mode", ["equal", "weighted_by_self", "weighted"])
def test_base_native_measure_keeps_disaggregation_for_coarser_edits(populated, mode):
    if mode == "weighted":
        basis = definition("measure_fqty")
        basis["objectId"] = "base_weight"
        basis["name"] = "Base weight"
        basis["displayName"] = "Base weight"
        response = populated.post(ROOT + "/measures", json=basis)
        assert response.status_code == 201, response.text
    payload = definition("measure_fqty")
    payload["definition"]["allocation"] = {
        "mode": mode,
        **({"basisMeasureId": "base_weight"} if mode == "weighted" else {}),
    }
    response = populated.put(
        ROOT + "/measures/measure_fqty", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 200, response.text
    assert response.json()["definition"]["allocation"]["mode"] == mode
    assert populated.post(ROOT + "/validate").json()["valid"] is True


def test_hidden_business_fields_default_without_inventing_units(populated):
    payload = definition("measure_sqty")
    payload["objectId"] = "hidden_business"
    payload["business"] = {}
    response = populated.post(ROOT + "/measures", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["business"] == {"unit": "EA", "aliases": []}
    # An undeclared source unit remains unknown rather than being guessed.
    source = definition("src_sales")
    source["objectId"] = "src_unknown_unit"
    for column in source["definition"]["columns"]:
        column.pop("unit", None)
    assert populated.post(ROOT + "/sources", json=source).status_code == 201
    transaction = definition("tx_sales")
    transaction["objectId"] = "tx_unknown_unit"
    transaction["definition"]["sourceId"] = source["objectId"]
    assert populated.post(ROOT + "/transactions", json=transaction).status_code == 201
    payload["objectId"] = "measure_unknown_unit"
    payload["definition"]["transactionId"] = transaction["objectId"]
    response = populated.post(ROOT + "/measures", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["business"] == {"unit": None, "aliases": []}


def test_new_referenced_measure_defaults_to_weighted_by_self(populated):
    payload = definition("measure_sqty")
    payload["objectId"] = "default_self"
    payload["definition"].pop("allocation", None)
    response = populated.post(ROOT + "/measures", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["allocation"]["mode"] == "weighted_by_self"


def test_calculate_first_defaults_to_self_but_aggregate_first_does_not():
    from backend.app.schemas.measures import CalculatedMeasureDefinition

    d = {
        "measureType": "calculated",
        "nativePlanningLevelId": "native",
        "calculationType": "CalculateFirst",
        "formula": {"displayText": "1", "expression": {"type": "literal", "value": 1}},
        "aggregation": {"nonTime": "sum", "time": "sum"},
    }
    assert (
        CalculatedMeasureDefinition.model_validate(d).allocation.mode
        == "weighted_by_self"
    )
    d.update(calculationType="AggregateFirst", aggregation=None)
    assert CalculatedMeasureDefinition.model_validate(d).allocation is None


@pytest.mark.parametrize("allocation", [None, {}, {"mode": "none"}, {"mode": "equal"}])
def test_saved_measure_policies_keep_their_previous_meaning(allocation):
    from backend.app.services.measure_types import normalize_measure

    payload = definition("measure_sqty")
    payload["definition"]["allocation"] = allocation
    normalized = normalize_measure(payload)["definition"]["allocation"]
    assert normalized["mode"] == (
        "equal" if allocation == {"mode": "equal"} else "none"
    )
    payload["definition"].pop("allocation")
    assert normalize_measure(payload)["definition"]["allocation"]["mode"] == "none"
