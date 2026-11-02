from backend.app.engine.relationships import LevelRelationshipAnalyzer
from backend.app.schemas.measures import Allocation, ReferencedMeasureDefinition


def source_level(oid, source="matrix", keys=("product", "location"), bindings=()):
    return {
        "objectId": oid,
        "kind": "planningLevel",
        "name": oid,
        "definition": {
            "population": {"operation": "source", "sourceId": source},
            "grain": {
                "keyIds": list(keys),
                "columns": [
                    {
                        "grainColumnId": key,
                        "name": key,
                        "populationRef": {"columnId": key},
                    }
                    for key in keys
                ],
            },
            "populationAttributes": [],
            "dimensionBindings": list(bindings),
        },
    }


def test_no_relationship_is_inferred_from_equal_names():
    a = source_level("a", "source_a")
    b = source_level("b", "source_b", ("product",))
    analyzer = LevelRelationshipAnalyzer([a, b])
    assert analyzer.analyze("a", "b")["relationship"] == "incompatible"
    assert not analyzer.can_edit(
        {"definition": {"editable": True, "nativePlanningLevelId": "a"}}, "b"
    )


def test_key_projection_without_dimension_bindings_and_edit_direction():
    base = source_level("base")
    coarse = {
        "objectId": "coarse",
        "kind": "planningLevel",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourcePlanningLevelId": "base",
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
            "dimensionBindings": [],
        },
    }
    analyzer = LevelRelationshipAnalyzer([base, coarse])
    assert analyzer.analyze("base", "coarse")["relationship"] == "roll_up"
    assert analyzer.analyze("coarse", "base")["relationship"] == "roll_down"
    assert analyzer.can_edit(
        {"definition": {"editable": True, "nativePlanningLevelId": "base"}}, "coarse"
    )
    assert not analyzer.can_edit(
        {"definition": {"editable": True, "nativePlanningLevelId": "coarse"}}, "base"
    )


def test_attribute_hierarchy_requires_binding_and_uses_dimension_identity():
    binding = {
        "dimensionId": "product_dim",
        "keyMappings": [{"grainColumnId": "product", "dimensionColumnId": "id"}],
        "selectedAttributeColumnIds": [],
    }
    base = source_level("base", bindings=[binding])
    brand = source_level("brand", "products", ("brand",))
    dim = {
        "objectId": "product_dim",
        "kind": "dimension",
        "isTimeDim": False,
        "definition": {
            "sourceId": "products",
            "keyColumnIds": ["id"],
            "columns": [
                {"columnId": "id", "sourceColumnId": "product"},
                {"columnId": "brand", "sourceColumnId": "brand"},
            ],
        },
    }
    analyzer = LevelRelationshipAnalyzer([base, brand, dim])
    path = analyzer.analyze("base", "brand")
    assert path["relationship"] == "roll_up" and path["requiresDimensionLookup"]
    brand["definition"]["dimensionBindings"] = [
        {
            "dimensionId": "product_dim",
            "keyMappings": [{"grainColumnId": "brand", "dimensionColumnId": "brand"}],
            "selectedAttributeColumnIds": [],
        }
    ]
    analyzer = LevelRelationshipAnalyzer([base, brand, dim])
    assert analyzer.analyze("base", "brand")["relationship"] == "roll_up"
    assert analyzer.analyze("brand", "base")["relationship"] == "roll_down"
    assert not analyzer.mapping("brand", "base")["product"]
    base["definition"]["dimensionBindings"] = []
    assert (
        LevelRelationshipAnalyzer([base, brand, dim]).analyze("base", "brand")[
            "relationship"
        ]
        == "incompatible"
    )


def test_null_disaggregation_is_canonical_repeat():
    assert Allocation(mode=None).mode == "none"
    assert Allocation().mode == "none"
    payload = {
        "measureType": "referenced",
        "transactionId": "tx",
        "valueColumnId": "v",
        "nativePlanningLevelId": "base",
        "resultType": {"type": "integer"},
        "aggregation": {"nonTime": "avg", "time": "max"},
        "allocation": None,
    }
    definition = ReferencedMeasureDefinition.model_validate(payload)
    assert definition.allocation.mode == "none"
    assert (
        definition.aggregation.nonTime == "avg" and definition.aggregation.time == "max"
    )
