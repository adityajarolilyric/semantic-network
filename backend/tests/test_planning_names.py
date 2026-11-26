from backend.app.engine.populations.planner import DimensionPlanner


def test_collision_alias_cannot_capture_real_source_column_name():
    columns = [
        {"columnId": "first", "name": "id", "ownerName": "Products"},
        {"columnId": "second", "name": "id", "ownerName": "Time"},
        {"columnId": "literal", "name": "Time__id", "ownerName": "Other"},
    ]
    DimensionPlanner.assign_output_names(columns)
    assert columns[0]["name"] == "id"
    assert columns[2]["name"] == "Time__id"
    assert columns[1]["name"].startswith("Time__id__")
    assert len({c["name"] for c in columns}) == 3


def test_legacy_dimension_alias_uses_origin_not_prefix_stripping():
    objects = {
        "product": {
            "definition": {"columns": [{"columnId": "name", "name": "product_name"}]}
        }
    }
    legacy = {
        "name": "PLT__product_name",
        "lineage": {
            "kind": "dimensionMembers",
            "memberCatalogId": "product",
            "columnId": "name",
        },
    }
    assert DimensionPlanner.semantic_name(legacy, objects) == "product_name"
    # A legitimate source name containing '__' must never be modified by guessing.
    assert (
        DimensionPlanner.semantic_name({"name": "PLT__product_name"}, objects)
        == "PLT__product_name"
    )
