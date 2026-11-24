"""Infer independent calendar series from the semantic grain, never from data rows."""

from copy import deepcopy

from backend.app.engine.graph.temporal_context import TemporalContextResolver
from backend.app.validation.graph import SemanticGraphValidator
from backend.app.engine.measures.temporal_calendar import calendar_slice_keys


def calendar_model(keys=("product", "location", "week", "month")):
    calendar_columns = [
        ("date", "Date"),
        ("week", "String"),
        ("month", "String"),
        ("week_start", "Date"),
        ("week_end", "Date"),
    ]
    sources = [
        {
            "objectId": sid,
            "kind": "source",
            "name": sid,
            "definition": {
                "tableName": sid,
                "columns": [
                    {
                        "columnId": cid,
                        "name": cid,
                        "physicalType": typ,
                        "nullable": False,
                    }
                    for cid, typ in cols
                ],
            },
        }
        for sid, cols in [
            ("calendar", calendar_columns),
            ("business", [("product", "String"), ("location", "String")]),
        ]
    ]
    calendar = {
        "objectId": "Time",
        "kind": "dimensionMembers",
        "name": "Time",
        "isTimeDim": True,
        "definition": {
            "sourceId": "calendar",
            "keyColumnIds": ["date"],
            "attributeColumnIds": [cid for cid, _ in calendar_columns],
            "labelColumnId": "date",
            "columns": [
                {
                    "columnId": cid,
                    "name": cid,
                    "displayName": cid,
                    "sourceColumnId": cid,
                }
                for cid, _ in calendar_columns
            ],
            "time": {
                "baseGrain": "day",
                "dateColumnId": "date",
                "orderingColumnId": "date",
            },
        },
    }

    def column(cid, grain):
        return {
            "grainColumnId" if grain else "columnId": cid,
            "name": cid,
            "populationRef": {
                "factorId": "b" if cid in {"product", "location"} else "t",
                "columnId": cid,
            },
        }

    level = {
        "objectId": "base",
        "kind": "dimension",
        "name": "ProductLocationTime",
        "definition": {
            "grain": {
                "keyIds": list(keys),
                "columns": [column(cid, True) for cid in keys],
            },
            "population": {
                "operation": "cartesian_product",
                "factors": [
                    {"factorId": "b", "sourceId": "business"},
                    {"factorId": "t", "sourceId": "calendar"},
                ],
            },
            "populationAttributes": [
                column(cid, False)
                for cid, _ in calendar_columns
                if cid not in keys and cid != "date"
            ],
            "memberBindings": [],
        },
    }
    return [*sources, calendar, level]


def resolve(objects):
    context = SemanticGraphValidator(objects)
    return TemporalContextResolver(context).resolve(context.objects["base"])


def test_week_order_and_only_non_time_grain_partitions():
    resolution = resolve(calendar_model())
    assert resolution["context"] == {
        "timeKeyId": "week",
        "orderColumnId": "week_start",
        "periodUnit": "week",
        "partitionColumnIds": ["product", "location"],
        "resetColumnId": None,
        "minimumPeriods": 1,
    }
    assert resolution["timeKeyIds"] == ["week", "month"]
    assert "product, location" in resolution["summary"]


def test_calendar_slices_are_ordering_keys_not_implicit_resets():
    context = SemanticGraphValidator(calendar_model())
    resolver = TemporalContextResolver(context)
    settings = resolver.resolve(context.objects["base"])["context"]
    assert calendar_slice_keys(resolver.relationships, "base", settings) == [
        "week",
        "month",
    ]
    assert (
        calendar_slice_keys(
            resolver.relationships, "base", {**settings, "resetColumnId": "month"}
        )
        == []
    )
    assert (
        calendar_slice_keys(
            resolver.relationships,
            "base",
            {**settings, "partitionColumnIds": ["product", "location", "month"]},
        )
        == []
    )


def test_finest_time_grain_and_single_series():
    day = resolve(calendar_model(("date", "week", "month")))
    assert day["context"]["timeKeyId"] == day["context"]["orderColumnId"] == "date"
    assert day["context"]["periodUnit"] == "day"
    assert day["context"]["partitionColumnIds"] == []


def test_unmarked_columns_do_not_create_a_calendar_by_name():
    objects = calendar_model()
    objects = [obj for obj in objects if obj["objectId"] != "Time"]
    resolution = resolve(objects)
    assert resolution["context"] is None
    assert "No Time key" in resolution["reason"]


def test_calendar_lineage_survives_friendly_output_labels():
    objects = calendar_model()
    level = objects[-1]["definition"]
    next(c for c in level["grain"]["columns"] if c["grainColumnId"] == "week")[
        "name"
    ] = "Fiscal period"
    next(c for c in level["populationAttributes"] if c["columnId"] == "week_start")[
        "name"
    ] = "Begins"
    assert resolve(objects)["context"]["orderColumnId"] == "week_start"


def test_ambiguous_period_start_requires_explicit_choice():
    objects = calendar_model()
    source = objects[0]
    source["definition"]["columns"].append(
        {
            "columnId": "other_week_start",
            "name": "other_week_start",
            "physicalType": "Date",
            "nullable": False,
        }
    )
    objects[2]["definition"]["columns"].append(
        {
            "columnId": "other_week_start",
            "name": "other_week_start",
            "displayName": "Other week start",
            "sourceColumnId": "other_week_start",
        }
    )
    objects[-1]["definition"]["populationAttributes"].append(
        {
            "columnId": "other_week_start",
            "name": "other_week_start",
            "populationRef": {"factorId": "t", "columnId": "other_week_start"},
        }
    )
    resolution = resolve(objects)
    assert resolution["context"] is None
    assert "unique chronological" in resolution["reason"]


def test_saved_overrides_and_resets_are_preserved():
    context = SemanticGraphValidator(calendar_model())
    explicit = {
        "timeKeyId": "week",
        "orderColumnId": "week_end",
        "periodUnit": "week",
        "partitionColumnIds": ["product"],
        "resetColumnId": "month",
        "minimumPeriods": 2,
    }
    assert (
        TemporalContextResolver(context).resolve(
            context.objects["base"], deepcopy(explicit)
        )["context"]
        == explicit
    )


def test_explicit_native_index_works_without_lookup_dimension():
    objects = calendar_model(("product", "week"))
    objects = [obj for obj in objects if obj["objectId"] != "Time"]
    objects[0]["definition"]["columns"][1].update(name="ordinal", physicalType="Int64")
    grain = objects[-1]["definition"]
    grain["grain"]["columns"][1]["name"] = "ordinal"
    grain["timeColumnId"] = "week"
    assert resolve(objects)["context"] == {
        "timeKeyId": "week",
        "orderColumnId": "week",
        "periodUnit": "index",
        "partitionColumnIds": ["product"],
        "resetColumnId": None,
        "minimumPeriods": 1,
    }


def test_validation_and_save_infer_the_same_context(populated):
    from .conftest import ROOT

    response = populated.post(
        ROOT + "/measures/formula/validate",
        json={
            "objectId": "running_forecast",
            "name": "Running Forecast",
            "displayText": 'CUMULATIVE([FQTY], "SUM")',
            "nativeGrainId": "pl_plt",
            "calculationType": "CalculateFirst",
        },
    )
    assert response.status_code == 200, response.text
    validation = response.json()
    assert validation["valid"], validation
    inferred = validation["temporalResolution"]["context"]
    assert inferred["timeKeyId"] == inferred["orderColumnId"] == "plt_date"
    assert inferred["partitionColumnIds"] == ["plt_product", "plt_location"]
    response = populated.post(
        ROOT + "/measures",
        json={
            "objectId": "running_forecast",
            "name": "Running Forecast",
            "displayName": "Running Forecast",
            "business": {},
            "definition": {
                "measureType": "calculated",
                "nativeDimensionId": "pl_plt",
                "calculationType": "CalculateFirst",
                "aggregation": {"nonTime": "sum", "time": "last"},
                "formula": {
                    "displayText": 'CUMULATIVE([FQTY], "SUM")',
                    "languageVersion": "planning-v1",
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["temporal"] == inferred
