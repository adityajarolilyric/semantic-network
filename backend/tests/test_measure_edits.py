import pytest
from pydantic import ValidationError
from backend.app.schemas.decision_view import DecisionViewEdit
from backend.app.engine.decision.edit_planner import MeasureEditPlanner
from backend.app.engine.decision.planner import DecisionViewPlanner
from .test_relationships import source_level


def test_edit_payload_is_a_finite_value_for_exactly_one_displayed_cell():
    payload = {
        "view": {
            "rows": [{"memberCatalogId": "Product", "columnId": "product"}],
            "measures": ["q"],
        },
        "measureId": "q",
        "memberValues": ["P1"],
        "value": 10,
        "generationId": "run_old",
        "revisionVersion": 1,
    }
    assert DecisionViewEdit.model_validate(payload).value == 10
    for patch in (
        {"memberValues": []},
        {"value": float("nan")},
        {"measureId": "other"},
    ):
        with pytest.raises(ValidationError):
            DecisionViewEdit.model_validate({**payload, **patch})


def test_edit_capabilities_follow_virtual_grain_not_selected_storage_level():
    dims = [
        {
            "objectId": name,
            "kind": "dimensionMembers",
            "name": name,
            "isTimeDim": False,
            "definition": {
                "sourceId": name + "_source",
                "columns": [
                    {"columnId": key, "sourceColumnId": key, "displayName": key}
                ],
                "keyColumnIds": [key],
                "attributeColumnIds": [],
            },
        }
        for name, key in [("Product", "product"), ("Location", "location")]
    ]
    base = source_level(
        "base",
        bindings=[
            {
                "memberCatalogId": dim["objectId"],
                "keyMappings": [
                    {
                        "grainColumnId": dim["definition"]["keyColumnIds"][0],
                        "memberColumnId": dim["definition"]["keyColumnIds"][0],
                    }
                ],
                "selectedAttributeColumnIds": [],
            }
            for dim in dims
        ],
    )
    coarse = {
        "objectId": "coarse",
        "kind": "dimension",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourceDimensionId": "base",
            },
            "grain": {
                "keyIds": ["p"],
                "columns": [
                    {
                        "grainColumnId": "p",
                        "populationRef": {"sourceGrainColumnId": "product"},
                    }
                ],
            },
            "memberBindings": [],
        },
    }
    measure = {
        "objectId": "q",
        "kind": "measure",
        "definition": {
            "measureType": "referenced",
            "nativeDimensionId": "coarse",
            "editable": True,
        },
    }
    objects = [base, coarse, *dims, measure]
    snapshot = {
        "base": {"measureColumns": {"q": "value"}},
        "coarse": {"measureColumns": {"q": "value"}},
    }
    request = DecisionViewEdit.model_validate(
        {
            "view": {
                "rows": [{"memberCatalogId": "Product", "columnId": "product"}],
                "measures": ["q"],
            },
            "measureId": "q",
            "memberValues": ["P1"],
            "value": 10,
            "generationId": "run_old",
            "revisionVersion": 1,
        }
    )
    fields = DecisionViewPlanner(objects, "base").fields(request.view)
    planner = MeasureEditPlanner(objects, "base", fields)
    assert planner.capability("q", snapshot)["editable"]
    assert not planner.capability("q", snapshot, False)["editable"]
    assert not planner.capability("q", {})["editable"]
    request.view.columns.append(
        type(request.view.rows[0])(memberCatalogId="Location", columnId="location")
    )
    fields = DecisionViewPlanner(objects, "base").fields(request.view)
    assert not MeasureEditPlanner(objects, "base", fields).capability("q", snapshot)[
        "editable"
    ]
