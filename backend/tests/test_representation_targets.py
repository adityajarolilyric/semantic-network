from copy import deepcopy

from backend.app.engine.decision_attributes import DecisionAttributeCatalog
from backend.app.engine.grain_contexts import resolve_grain_contexts
from backend.app.engine.incremental import MaterialisationManifest
from backend.app.engine.representations import RepresentationPolicy
from .test_incremental import model


def with_projection(objects, object_id, name="Product"):
    parent = deepcopy(next(obj for obj in objects if obj["objectId"] == "base"))
    parent.update(objectId=object_id, name=name)
    parent["definition"]["population"] = {
        "operation": "distinct_projection",
        "sourceDimensionId": "base",
    }
    parent["definition"]["grain"] = {
        "keyIds": ["product"],
        "columns": [
            {
                "grainColumnId": "product",
                "name": "product",
                "populationRef": {"sourceGrainColumnId": "product"},
            }
        ],
    }
    objects.append(parent)
    return parent


def test_transaction_grains_are_shared_targets_but_unused_dimensions_are_not():
    objects, populations, managed = model()
    with_projection(objects, "product")
    with_projection(objects, "lookup")
    tx = next(obj for obj in objects if obj["objectId"] == "tx_sales")
    tx["definition"]["dimensionId"] = "product"
    tx["definition"]["keyMappings"] = [
        {"sourceColumnId": "product", "grainColumnId": "product"}
    ]
    populations.update(product={"artifactId": "p1"}, lookup={"artifactId": "l1"})
    resolved = resolve_grain_contexts(objects)
    policy = RepresentationPolicy(resolved, "base")
    assert policy.targets() == {"base", "product"}
    assert policy.includes("FQTY", "product")
    assert not policy.includes("FQTY", "lookup")
    manifest = MaterialisationManifest(objects, populations, managed, "base").all()
    assert set(manifest) == {"base", "product"}
    assert "FQTY" in manifest["product"]["evaluations"]


def test_no_base_allows_rollup_between_native_grains_without_finer_distribution():
    objects, _, _ = model()
    with_projection(objects, "product")
    tx = next(obj for obj in objects if obj["objectId"] == "tx_sales")
    tx["definition"]["dimensionId"] = "product"
    tx["definition"]["keyMappings"] = [
        {"sourceColumnId": "product", "grainColumnId": "product"}
    ]
    policy = RepresentationPolicy(resolve_grain_contexts(objects), None)
    assert policy.includes("FQTY", "product")
    assert not policy.includes("SQTY", "base")
    assert policy.includes("SQTY", "product")


def test_attribute_catalog_uses_only_native_base_and_preserves_aliases():
    objects, _, _ = model()
    with_projection(objects, "product")
    lookup = with_projection(objects, "lookup")
    lookup["definition"]["populationAttributes"] = [
        {
            "columnId": "hidden",
            "name": "Hidden",
            "populationRef": {"sourceOutputColumnId": "hidden"},
        }
    ]
    tx = next(obj for obj in objects if obj["objectId"] == "tx_sales")
    tx["definition"]["dimensionId"] = "product"
    tx["definition"]["keyMappings"] = [
        {"sourceColumnId": "product", "grainColumnId": "product"}
    ]
    result = DecisionAttributeCatalog(resolve_grain_contexts(objects), "base").options()
    columns = [
        col for dimension in result["dimensions"] for col in dimension["columns"]
    ]
    assert [col["columnId"] for col in columns] == ["product", "location"]
    assert {"dimensionId": "product", "columnId": "product"} in columns[0]["aliases"]
    assert {"dimensionId": "lookup", "columnId": "product"} in columns[0]["aliases"]
    assert {"transactionId": "tx_sales", "columnId": "product"} in columns[0]["aliases"]
    assert "Hidden" not in {col["name"] for col in columns}
    assert result["transactions"] == []


def test_equal_attribute_names_without_proven_identity_are_not_merged():
    objects, _, _ = model()
    base = next(obj for obj in objects if obj["objectId"] == "base")
    other = deepcopy(base)
    other.update(objectId="independent", name="Independent")
    other["definition"]["population"]["sourceId"] = "different_population"
    objects.append(other)
    tx = next(obj for obj in objects if obj["objectId"] == "tx_sales")
    tx["definition"]["dimensionId"] = "independent"
    result = DecisionAttributeCatalog(resolve_grain_contexts(objects), "base").options()
    assert {dim["dimensionId"] for dim in result["dimensions"]} == {
        "base",
        "independent",
    }
    assert (
        sum(
            col["name"] == "product"
            for dim in result["dimensions"]
            for col in dim["columns"]
        )
        == 2
    )


def test_bound_transactions_never_generate_independent_grain_contexts():
    objects, _, _ = model()
    resolved = resolve_grain_contexts(objects)
    assert not any(obj["kind"] == "grain" for obj in resolved)
    assert {
        obj["definition"]["nativeGrainId"]
        for obj in resolved
        if obj["kind"] == "transaction"
    } == {"base"}
    assert RepresentationPolicy(resolved, None).targets() == {"base"}
