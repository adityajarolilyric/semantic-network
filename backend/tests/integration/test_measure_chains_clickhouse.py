# ruff: noqa: F811
"""Nested formula grain boundaries, reuse, edits and incremental invalidation."""

from collections import Counter

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_representation_targets_clickhouse import authored_network  # noqa: F401
from .test_decision_view_clickhouse import query_view, field
from .test_incremental_clickhouse import run_incremental
from .test_measures_clickhouse import BASE, assert_post, update_measure
from .test_measure_edits_clickhouse import editable, edit
from .test_managed_clickhouse import await_run
from backend.app.engine.measures.compiler import MeasureCompiler

pytestmark = pytest.mark.integration


def formula(
    client, name, source, calculation="AggregateFirst", native="base", aggregation=None
):
    return assert_post(
        client,
        "/measures",
        {
            "objectId": name,
            "kind": "measure",
            "name": name,
            "displayName": name,
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": native,
                "calculationType": calculation,
                "formula": {"displayText": source, "languageVersion": "planning-v1"},
                **({"aggregation": aggregation} if aggregation else {}),
            },
        },
    )


AGG = {
    "nonTime": "sum",
    "time": "sum",
    "nullInputPolicy": "ignore",
    "allNullResult": "null",
}


def test_mixed_chain_respects_each_boundary_and_reuses_diamond(
    authored_network, monkeypatch
):
    client, _, _, _ = authored_network
    calls = Counter()
    original = MeasureCompiler.compile_calculated_measure

    def counted(self, measure, target):
        calls[(id(self), measure["objectId"], target)] += 1
        return original(self, measure, target)

    monkeypatch.setattr(MeasureCompiler, "compile_calculated_measure", counted)
    formula(client, "DoubleAF", "[ErrorAF] * 2")
    formula(
        client, "FrozenCF", "[DoubleAF] * [DoubleAF]", "CalculateFirst", aggregation=AGG
    )
    formula(client, "Top", "[FrozenCF] + [DoubleAF]")
    built = run_incremental(client)
    assert built["state"] == "succeeded", built
    assert calls and max(calls.values()) == 1
    view = query_view(
        client, [field("Product", "brand")], measures=["DoubleAF", "FrozenCF", "Top"]
    )
    assert view.status_code == 200, view.text
    assert view.json()["rows"] == [
        ["A", "120.0", "15200.0", "15320.0"],
        ["B", "60.0", "3600.0", "3660.0"],
    ]
    # A brand-grouped AF recomputes its own inputs, while CF retains native squares.
    assert run_incremental(client)["reused"] is True
    update_measure(
        client, "DoubleAF", lambda d: d["formula"].update(displayText="[ErrorAF] * 3")
    )
    rebuilt = run_incremental(client)
    assert rebuilt["state"] == "succeeded", rebuilt
    view = query_view(client, [field("Product", "brand")], measures=["Top"])
    assert view.json()["rows"] == [["A", "34380.0"], ["B", "8190.0"]]


def test_edits_refresh_all_nested_dependants_and_keep_unrelated_values(
    authored_network,
):
    client, _, _, _ = authored_network
    editable(client, "FQTY", "equal")
    formula(client, "Double", "[FQTY] * 2")
    formula(client, "Triple", "[Double] + [FQTY]", "CalculateFirst", aggregation=AGG)
    formula(client, "Final", "[Triple] + [Double]")
    assert run_incremental(client)["state"] == "succeeded"
    run = await_run(client, BASE, edit(client, value=200))
    assert run["state"] == "succeeded", run
    result = query_view(
        client,
        [field("Product", "brand")],
        [field("Location", "zone")],
        ["FQTY", "Double", "Triple", "Final"],
    )
    assert result.status_code == 200, result.text
    assert result.json()["rows"][0] == ["A", "Z1", "200.0", "400.0", "600.0", "1000.0"]
    previous = result.json()["rows"]
    assert run_incremental(client)["reused"] is True
    assert (
        query_view(
            client,
            [field("Product", "brand")],
            [field("Location", "zone")],
            ["FQTY", "Double", "Triple", "Final"],
        ).json()["rows"]
        == previous
    )


def test_nulls_and_typed_states_survive_calculated_dependencies(authored_network):
    client, _, _, _ = authored_network
    formula(client, "Missing", "[FQTY] / 0")
    formula(client, "MissingAgain", "[Missing] + 1", "CalculateFirst", aggregation=AGG)
    formula(
        client,
        "Label",
        'IF([FQTY] > 0, "Positive", "Negative")',
        "CalculateFirst",
        aggregation={**AGG, "nonTime": "single_value", "time": "single_value"},
    )
    formula(
        client,
        "UpperLabel",
        "UPPER([Label])",
        "CalculateFirst",
        aggregation={**AGG, "nonTime": "single_value", "time": "single_value"},
    )
    assert run_incremental(client)["state"] == "succeeded"
    result = query_view(
        client, [field("Location", "zone")], measures=["MissingAgain", "UpperLabel"]
    )
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == [["Z1", None, "POSITIVE"], ["Z2", None, None]]
    assert result.json()["cellStates"][1][1] == "mixed"
