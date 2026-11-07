from copy import deepcopy

from backend.app.engine.pivot_suggestions import PivotSuggestionDetector


def columns(*names):
    return [
        {"columnId": f"c{index}", "name": name, "physicalType": "String"}
        for index, name in enumerate(names)
    ]


def test_separate_attribute_rows_have_identifier_record_keys_and_all_outputs():
    cols = columns("customer_id", "attribute_id", "attribute_value")
    rows = [
        ("C1", "Channel", "Retail"),
        ("C1", "Sold To", "S1"),
        ("C2", "Channel", "Wholesale"),
        ("C2", "Sold To", "S2"),
    ]
    original = deepcopy(rows)
    result = PivotSuggestionDetector().suggest("customers", cols, rows)
    assert result.confidence == "high"
    assert result.complete
    assert result.pivot.recordKeyColumnIds == ["c0"]
    assert result.pivot.groupByColumnIds == ["c0"]
    assert result.pivot.pairs[0].attributeColumnId == "c1"
    assert result.pivot.pairs[0].valueColumnId == "c2"
    assert [out.name for out in result.pivot.pairs[0].outputs] == ["Channel", "Sold To"]
    assert rows == original
    assert result == PivotSuggestionDetector().suggest("customers", cols, rows)


def test_multiple_pairs_preserve_rows_and_numeric_retained_values():
    cols = columns(
        "product_attribute",
        "product_value",
        "location_attribute",
        "location_value",
        "week",
        "forecast_qty",
    )
    cols[-1]["physicalType"] = "Float64"
    result = PivotSuggestionDetector().suggest(
        "forecast",
        cols,
        [
            ("Brand", "B1", "Zone", "Z1", "W1", 100),
            ("Brand", "B2", "Zone", "Z2", "W1", 100),
        ],
    )
    assert len(result.pivot.pairs) == 2
    assert result.pivot.recordKeyColumnIds == []
    assert result.pivot.groupByColumnIds == ["c4", "c5"]
    assert [pair.outputs[0].name for pair in result.pivot.pairs] == ["Brand", "Zone"]


def test_wide_tables_ambiguous_partners_and_empty_inputs_are_not_guessed():
    detector = PivotSuggestionDetector()
    assert (
        detector.suggest(
            "wide",
            columns("product", "promo_type", "value"),
            [("P1", "Discount", "10")],
        ).pivot
        is None
    )
    assert (
        detector.suggest(
            "ambiguous",
            columns("attribute", "value_1", "value_2"),
            [("Brand", "B1", "B2")],
        ).pivot
        is None
    )
    assert detector.suggest("empty", columns("attribute", "value"), []).pivot is None
    too_large = detector.suggest(
        "large", columns("attribute", "value"), [("Brand", "B1")] * 5001
    )
    assert not too_large.complete and too_large.rowsExamined == 100
    assert too_large.pivot is not None and too_large.warnings


def test_collisions_are_named_safely_and_ambiguous_records_are_not_merged():
    result = PivotSuggestionDetector().suggest(
        "table",
        columns("id", "Brand", "attribute", "value"),
        [
            ("C1", "retained", "Brand", "B1"),
            ("C1", "retained", "Brand", "B2"),
        ],
    )
    assert result.pivot.pairs[0].outputs[0].name == "attribute_Brand"
    assert result.pivot.recordKeyColumnIds == []
    assert result.warnings


def test_only_sampled_selector_values_are_suggested_with_a_warning():
    result = PivotSuggestionDetector().suggest(
        "table",
        columns("attribute", "value"),
        [(f"Attribute_{i}", "Value") for i in range(201)],
    )
    assert result.rowsExamined == 100 and not result.complete
    assert {output.attributeValue for output in result.pivot.pairs[0].outputs} == {
        f"Attribute_{i}" for i in range(100)
    }
    assert any("Discover" in warning for warning in result.warnings)
