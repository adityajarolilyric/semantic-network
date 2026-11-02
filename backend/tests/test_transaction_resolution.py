from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.core.errors import MetadataError
from backend.app.engine.transaction_resolution import TransactionResolutionPlanner
from backend.app.schemas.transactions import TransactionDefinition


def config():
    return {
        "sourceId": "source",
        "planningLevelId": "native",
        "mappingMode": "resolve_multi_grain",
        "resolution": {
            "axes": [
                {
                    "grainColumnId": "product",
                    "rules": [
                        {
                            "ruleId": "fixed",
                            "sourceColumnId": "member",
                            "target": {"columnId": "product"},
                        }
                    ],
                }
            ],
            "values": [{"sourceColumnId": "quantity", "mode": "split"}],
            "orderColumnIds": ["sequence"],
        },
    }


def planner(definition=None):
    definition = TransactionDefinition.model_validate(
        definition or config()
    ).model_dump()
    level = {
        "objectId": "native",
        "kind": "planningLevel",
        "definition": {
            "population": {"operation": "source", "sourceId": "source"},
            "grain": {
                "keyIds": ["product"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "product",
                        "populationRef": {"columnId": "member"},
                    }
                ],
            },
            "dimensionBindings": [],
        },
    }
    cols = [
        {"columnId": cid, "name": cid, "physicalType": typ, "nullable": False}
        for cid, typ in [
            ("member", "String"),
            ("quantity", "Int64"),
            ("sequence", "Int64"),
        ]
    ]
    return TransactionResolutionPlanner(
        [level], {"objectId": "tx", "definition": definition}, cols
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "no_order",
        "no_axis_rules",
        "distributed_order",
        "duplicate_selector",
        "direct_with_resolution",
    ],
)
def test_resolution_contract_rejects_incomplete_or_ambiguous_configuration(mutation):
    d = config()
    r = d["resolution"]
    if mutation == "no_order":
        r["orderColumnIds"] = []
    elif mutation == "no_axis_rules":
        r["axes"][0]["rules"] = []
    elif mutation == "distributed_order":
        r["orderColumnIds"] = ["quantity"]
    elif mutation == "direct_with_resolution":
        d["mappingMode"] = "direct"
    else:
        r["axes"][0]["rules"] = [
            {
                "ruleId": "selector",
                "kind": "level_value",
                "selectorColumnId": "member",
                "valueColumnId": "member",
                "selectors": [{"value": "id", "target": {"columnId": "product"}}] * 2,
            }
        ]
    with pytest.raises(ValidationError):
        TransactionDefinition.model_validate(d)


def test_resolution_requires_every_native_axis_and_explicit_sequence():
    p = planner()
    p.config["axes"][0]["grainColumnId"] = "wrong"
    with pytest.raises(MetadataError, match="every native grain axis"):
        p.validate()
    d = config()
    d["resolution"]["orderColumnIds"] = ["member"]
    with pytest.raises(MetadataError, match="integer or date"):
        planner(d)


def test_resolution_profile_validation_and_stable_schema():
    p = planner()
    p.validate_profiles([(1,)], 64)
    with pytest.raises(MetadataError, match="no constraint"):
        p.validate_profiles([(0,)], 64)
    columns = p.schema()
    assert columns[0]["columnId"].startswith("txkey_")
    assert columns[1]["columnId"] == "quantity"
    assert columns[1]["physicalType"] == "Float64"
    assert columns == planner(deepcopy(config())).schema()


def test_date_range_contract_requires_time_policy_and_one_exclusive_axis():
    d = config()
    axis = d["resolution"]["axes"][0]
    axis["rules"] = []
    axis["dateRange"] = {
        "dimensionId": "Time",
        "calendarKeyColumnId": "week",
        "startColumnId": "start",
        "endColumnId": "end",
    }
    with pytest.raises(ValidationError, match="Across dates"):
        TransactionDefinition.model_validate(d)
    d["resolution"]["values"][0]["timeMode"] = "split"
    parsed = TransactionDefinition.model_validate(d)
    assert parsed.resolution.axes[0].dateRange.calendarKeyColumnId == "week"
    axis["allowAll"] = True
    with pytest.raises(ValidationError, match="replace the matching rules"):
        TransactionDefinition.model_validate(d)


def test_range_calendar_mapping_respects_the_bound_native_time_key():
    level = {
        "objectId": "native",
        "kind": "planningLevel",
        "definition": {
            "population": {"operation": "source", "sourceId": "population"},
            "grain": {
                "keyIds": ["week"],
                "columns": [
                    {
                        "grainColumnId": "week",
                        "name": "Week",
                        "populationRef": {"columnId": "week"},
                    }
                ],
            },
            "dimensionBindings": [
                {
                    "dimensionId": "Time",
                    "keyMappings": [
                        {"grainColumnId": "week", "dimensionColumnId": "week"}
                    ],
                    "selectedAttributeColumnIds": [],
                }
            ],
        },
    }
    dimension = {
        "objectId": "Time",
        "kind": "dimension",
        "isTimeDim": True,
        "definition": {
            "sourceId": "calendar",
            "columns": [
                {"columnId": "day", "sourceColumnId": "day"},
                {"columnId": "week", "sourceColumnId": "week"},
            ],
            "keyColumnIds": ["day"],
            "attributeColumnIds": ["week"],
            "time": {
                "baseGrain": "day",
                "dateColumnId": "day",
                "orderingColumnId": "day",
            },
        },
    }
    calendar = {
        "objectId": "calendar",
        "kind": "source",
        "definition": {"columns": [{"columnId": "day", "physicalType": "Date32"}]},
    }
    d = config()
    d["resolution"]["axes"] = [
        {
            "grainColumnId": "week",
            "dateRange": {
                "dimensionId": "Time",
                "calendarKeyColumnId": "day",
                "startColumnId": "start",
                "endColumnId": "end",
            },
        }
    ]
    d["resolution"]["values"][0]["timeMode"] = "split"
    columns = [
        {"columnId": cid, "name": cid, "physicalType": typ}
        for cid, typ in [
            ("quantity", "Float64"),
            ("sequence", "Int64"),
            ("start", "Date32"),
            ("end", "Date32"),
        ]
    ]
    tx = {
        "objectId": "tx",
        "definition": TransactionDefinition.model_validate(d).model_dump(),
    }
    with pytest.raises(MetadataError, match="existing dimension binding"):
        TransactionResolutionPlanner([level, dimension, calendar], tx, columns)
    tx["definition"]["resolution"]["axes"][0]["dateRange"]["calendarKeyColumnId"] = (
        "week"
    )
    assert TransactionResolutionPlanner(
        [level, dimension, calendar], tx, columns
    ).date_range.config
