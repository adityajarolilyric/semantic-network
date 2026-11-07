from copy import deepcopy

import pytest

from backend.app.core.errors import MetadataError
from backend.app.engine.compatibility import MeasureCompatibilityAnalyzer
from backend.app.engine.measure_dependencies import MeasureDependencies
from .conftest import ROOT
from .test_incremental import model
from .test_metadata_api import definition


def ref(mid):
    return {"type": "measureRef", "measureId": mid}


def calculated_payload(mid, dependency="measure_error", calculation="AggregateFirst"):
    payload = definition("measure_error")
    payload.update(objectId=mid, name=mid, displayName=mid)
    payload["definition"].update(
        calculationType=calculation,
        formula={"displayText": f"[{dependency}]", "expression": ref(dependency)},
    )
    if calculation == "CalculateFirst":
        payload["definition"]["aggregation"] = {
            "nonTime": "sum",
            "time": "sum",
            "nullInputPolicy": "ignore",
            "allNullResult": "null",
        }
    return payload


def test_dag_orders_shared_dependencies_once_and_reports_named_cycles():
    objects, _, _ = model()
    leaf = next(obj for obj in objects if obj["objectId"] == "Error")
    for mid, children in [
        ("left", ["Error"]),
        ("right", ["Error"]),
        ("top", ["left", "right"]),
    ]:
        item = deepcopy(leaf)
        item.update(objectId=mid, name=mid.title())
        item["definition"]["formula"]["expression"] = {
            "type": "call",
            "function": "max",
            "arguments": [ref(child) for child in children],
        }
        objects.append(item)
    graph = MeasureDependencies(objects)
    ordered = graph.ordered("top")
    assert len(ordered) == len(set(ordered)) == 6
    assert ordered.index("Error") < ordered.index("left") < ordered.index("top")
    assert graph.path("top", "Error") in (
        ("top", "left", "Error"),
        ("top", "right", "Error"),
    )
    leaf["definition"]["formula"]["expression"] = ref("top")
    with pytest.raises(MetadataError, match="dependency cycle") as error:
        MeasureDependencies(objects).ordered("top")
    assert "Top" in error.value.message and "Error" in error.value.message


def test_calculate_first_checks_its_dependencies_at_native_grain():
    objects, _, _ = model()
    base = next(obj for obj in objects if obj["objectId"] == "base")
    other = deepcopy(base)
    other.update(objectId="independent", name="Independent")
    other["definition"]["population"]["sourceId"] = "unrelated_source"
    objects.append(other)
    child = next(obj for obj in objects if obj["objectId"] == "Error")
    child["definition"]["nativeDimensionId"] = "independent"
    parent = deepcopy(child)
    parent.update(objectId="parent", name="Parent")
    parent["definition"].update(
        nativeDimensionId="base",
        calculationType="CalculateFirst",
        formula={"expression": ref("Error")},
    )
    objects.append(parent)
    result = MeasureCompatibilityAnalyzer(objects, "base").outcome("parent", "base")
    assert not result.available
    assert '"Parent"' in result.reason and '"Error"' in result.reason


def test_calculate_first_can_use_aggregate_first_and_picker_prevents_cycles(populated):
    response = populated.post(
        ROOT + "/measures",
        json=calculated_payload("nested", calculation="CalculateFirst"),
    )
    assert response.status_code == 201, response.text
    measure = response.json()
    options = populated.get(
        ROOT + "/measures/formula/measures",
        params={"nativeGrainId": "pl_pt", "objectId": "measure_error"},
    )
    assert options.status_code == 200, options.text
    nested = next(
        option for option in options.json() if option["measureId"] == "nested"
    )
    assert nested["available"] is False
    assert "Error" in nested["reason"] and "nested" in nested["reason"]
    check = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "another",
            "name": "Another",
            "nativeGrainId": "pl_pt",
            "calculationType": "CalculateFirst",
            "displayText": "[nested] + [Error]",
        },
    )
    assert check.status_code == 200 and check.json()["valid"], check.text
    # Direct and indirect cycles are rejected by the same authoritative save path.
    payload = definition("measure_error")
    payload["definition"]["formula"] = {
        "displayText": "[nested]",
        "expression": ref("nested"),
    }
    original = populated.get(ROOT + "/measures/measure_error").json()
    rejected = populated.put(
        ROOT + "/measures/measure_error",
        json=payload,
        headers={"If-Match": str(original["version"])},
    )
    assert rejected.status_code == 422
    assert "Error" in rejected.text and "nested" in rejected.text
    assert any(
        d["code"] == "dependency_cycle" for d in rejected.json()["error"]["details"]
    )
    assert measure["definition"]["calculationType"] == "CalculateFirst"


def test_picker_and_validation_show_readable_transitive_failure(populated):
    created = populated.post(ROOT + "/measures", json=calculated_payload("nested"))
    assert created.status_code == 201, created.text
    # Independent dimension has the same column labels but different lineage.
    payload = definition("pl_pt")
    payload.update(
        objectId="independent", name="Independent", displayName="Independent"
    )
    payload["definition"]["population"] = {
        "operation": "source",
        "sourceId": "src_sales",
    }
    payload["definition"]["grain"] = {
        "keyIds": ["qty"],
        "columns": [
            {
                "grainColumnId": "qty",
                "name": "qty",
                "populationRef": {"columnId": "sales_qty"},
            }
        ],
    }
    payload["definition"]["memberBindings"] = []
    payload["definition"]["populationAttributes"] = []
    response = populated.post(ROOT + "/dimensions", json=payload)
    assert response.status_code == 201, response.text
    options = populated.get(
        ROOT + "/measures/formula/measures", params={"nativeGrainId": "independent"}
    ).json()
    option = next(item for item in options if item["measureId"] == "nested")
    assert (
        not option["available"]
        and "nested" in option["reason"]
        and "Independent" in option["reason"]
    )


def test_dependency_depth_is_bounded_and_missing_references_are_readable():
    def item(index, child=None):
        return {
            "objectId": f"opaque_{index}",
            "kind": "measure",
            "name": f"Step {index}",
            "definition": {
                "formula": {
                    "expression": ref(f"opaque_{child}")
                    if child is not None
                    else {"type": "literal", "value": 1}
                }
            },
        }

    objects = [item(0)] + [item(index, index - 1) for index in range(1, 128)]
    assert len(MeasureDependencies(objects).ordered("opaque_127")) == 128
    objects.append(item(128, 127))
    with pytest.raises(MetadataError) as error:
        MeasureDependencies(objects).ordered("opaque_128")
    assert error.value.code == "measure_dependency_depth"
    with pytest.raises(
        MetadataError, match='"Step 1" references a measure that no longer exists'
    ):
        MeasureDependencies([item(1, 0)]).ordered("opaque_1")
