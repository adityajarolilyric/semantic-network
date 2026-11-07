from copy import deepcopy

import pytest

from backend.app.domain.transaction_measures import rebind_transaction_measures


def transaction(dimension="old"):
    return {
        "objectId": "sales",
        "kind": "transaction",
        "definition": {
            "sourceId": "source",
            "dimensionId": dimension,
            "mappingMode": "direct",
            "keyMappings": [
                {"sourceColumnId": "brand", "grainColumnId": dimension + "_brand"}
            ],
        },
    }


def reference(mid):
    return {
        "objectId": mid,
        "kind": "measure",
        "definition": {
            "measureType": "referenced",
            "transactionId": "sales",
            "nativeGrainId": "old",
        },
    }


def calculation(mid, dependencies, *, output="old", expression=None, dtype="number"):
    if expression is None:
        expression = {
            "type": "call",
            "function": "coalesce",
            "arguments": [
                {"type": "measureRef", "measureId": dependency}
                for dependency in dependencies
            ],
        }
    return {
        "objectId": mid,
        "kind": "measure",
        "definition": {
            "measureType": "calculated",
            "nativeGrainId": output,
            "resultType": {"type": dtype},
            "formula": {"expression": expression},
        },
    }


def test_closed_formula_dependency_chain_follows_same_source_keys():
    objects = [
        calculation("nested", ["price"]),
        calculation("price", ["qty", "value"]),
        reference("qty"),
        reference("value"),
    ]
    original = deepcopy(objects)
    moved = rebind_transaction_measures(transaction(), transaction("new"), objects)
    assert {measure["objectId"] for measure in moved} == {
        "qty",
        "value",
        "price",
        "nested",
    }
    assert all(measure["definition"]["nativeGrainId"] == "new" for measure in moved)
    assert objects == original  # previews cannot mutate the saved definitions


@pytest.mark.parametrize(
    "changes",
    [
        {"sourceId": "different"},
        {"mappingMode": "resolve_multi_grain"},
        {"pivot": {"enabled": True}},
        {"calendarResolution": {"startColumnId": "start"}},
        {"keyMappings": [{"grainColumnId": "new", "sourceColumnId": "week"}]},
    ],
)
def test_different_input_grains_never_relocate_calculations(changes):
    updated = transaction("new")
    updated["definition"].update(changes)
    moved = rebind_transaction_measures(
        transaction(), updated, [reference("qty"), calculation("price", ["qty"])]
    )
    assert [measure["objectId"] for measure in moved] == ["qty"]


def test_contextual_and_independent_calculations_keep_their_output_dimension():
    protected = [
        calculation("mixed", ["qty", "other_transaction_measure"]),
        calculation("explicit_output", ["qty"], output="base"),
        calculation("text", ["qty"], dtype="string"),
        calculation("constant", [], expression={"type": "literal", "value": 1}),
    ]
    for kind in ("attributeRef", "grainRef", "count"):
        node = {"type": kind, "arguments": []}
        protected.append(
            calculation(
                kind,
                [],
                expression={
                    "type": "binary",
                    "operator": "add",
                    "left": {"type": "measureRef", "measureId": "qty"},
                    "right": node,
                },
            )
        )
    moved = rebind_transaction_measures(
        transaction(), transaction("new"), [reference("qty"), *protected]
    )
    assert [measure["objectId"] for measure in moved] == ["qty"]
