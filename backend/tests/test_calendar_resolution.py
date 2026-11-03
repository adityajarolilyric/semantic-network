from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.core.errors import MetadataError
from backend.app.engine.calendar_resolution import CalendarResolutionPlanner
from backend.app.schemas.transactions import CalendarResolution, TransactionDefinition


def metadata():
    cols = [
        dict(columnId=c, name=c, physicalType=t, nullable=False)
        for c, t in [("start", "String"), ("end", "Date"), ("week", "String")]
    ]
    source = {
        "objectId": "calendar",
        "kind": "source",
        "definition": {
            "columns": [
                {
                    "columnId": "c_day",
                    "name": "date",
                    "physicalType": "Date",
                    "nullable": False,
                },
                {
                    "columnId": "c_week",
                    "name": "week",
                    "physicalType": "String",
                    "nullable": False,
                },
                {
                    "columnId": "c_month",
                    "name": "month",
                    "physicalType": "String",
                    "nullable": False,
                },
            ]
        },
    }
    dim = {
        "objectId": "time",
        "kind": "dimension",
        "isTimeDim": True,
        "definition": {
            "sourceId": "calendar",
            "keyColumnIds": ["day"],
            "attributeColumnIds": ["week", "month"],
            "columns": [
                {"columnId": c, "sourceColumnId": "c_" + c}
                for c in ["day", "week", "month"]
            ],
            "time": {
                "baseGrain": "day",
                "dateColumnId": "day",
                "orderingColumnId": "day",
            },
        },
    }
    tx = {
        "objectId": "promotion",
        "definition": {
            "calendarResolution": {
                "dimensionId": "time",
                "startColumnId": "start",
                "endColumnId": "end",
                "selectedColumnIds": ["week", "month"],
            }
        },
    }
    return [source, dim], tx, cols


def test_calendar_schema_preserves_sources_and_ids_when_selection_changes():
    objects, tx, cols = metadata()
    original = deepcopy((objects, tx, cols))
    p = CalendarResolutionPlanner(objects, tx, cols)
    schema = p.schema()
    week = next(c for c in schema if c.get("dimensionColumnId") == "week")
    assert week["name"] == "Calendar_week"
    assert len({c["name"] for c in schema}) == len(schema)
    assert (objects, tx, cols) == original
    tx["definition"]["calendarResolution"]["selectedColumnIds"] = ["week"]
    other = CalendarResolutionPlanner(objects, tx, cols)
    assert (
        next(c for c in other.schema() if c.get("dimensionColumnId") == "week")[
            "columnId"
        ]
        == week["columnId"]
    )


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("calendar", "daily_calendar_required"),
        ("endpoint", "invalid_resolution_column"),
        ("type", "invalid_range_type"),
        ("attribute", "invalid_calendar_key"),
    ],
)
def test_calendar_metadata_rejects_unresolvable_configuration(mutation, code):
    objects, tx, cols = metadata()
    cfg = tx["definition"]["calendarResolution"]
    if mutation == "calendar":
        objects[1]["definition"]["time"]["baseGrain"] = "week"
    if mutation == "endpoint":
        cfg["startColumnId"] = "missing"
    if mutation == "type":
        cols[0]["physicalType"] = "DateTime"
    if mutation == "attribute":
        cfg["selectedColumnIds"] = ["unknown"]
    with pytest.raises(MetadataError) as error:
        CalendarResolutionPlanner(objects, tx, cols)
    assert error.value.code == code


def test_absent_calendar_definition_retains_legacy_serialization():
    definition = TransactionDefinition(
        sourceId="s",
        planningLevelId="native",
        keyMappings=[{"sourceColumnId": "c", "grainColumnId": "g"}],
    )
    assert "calendarResolution" not in definition.model_dump()
    with pytest.raises(ValidationError):
        CalendarResolution(
            dimensionId="t",
            startColumnId="s",
            endColumnId="e",
            selectedColumnIds=["week", "week"],
        )
