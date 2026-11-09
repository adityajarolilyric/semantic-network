"""Storage ownership and interpretation boundaries for standalone Transactions."""

from copy import deepcopy

import pytest

from backend.app.engine.graph.grain_contexts import resolve_grain_contexts
from backend.app.engine.graph.source_storage import SourceStoragePolicy
from backend.app.engine.sql.snapshot_reads import snapshot_read


def model():
    columns = [
        {
            "columnId": "product",
            "name": "product",
            "physicalType": "String",
            "nullable": False,
        },
        {
            "columnId": "qty",
            "name": "qty",
            "physicalType": "Float64",
            "nullable": False,
        },
    ]
    objects = [
        {
            "kind": "source",
            "objectId": "source",
            "definition": {"tableName": "Forecast", "columns": columns},
        },
        {
            "kind": "transaction",
            "objectId": "tx",
            "name": "Forecast",
            "definition": {"sourceId": "source", "grainColumnIds": ["product"]},
        },
    ]
    graph = resolve_grain_contexts(objects)
    grain = next(obj["objectId"] for obj in graph if obj["kind"] == "grain")
    return (
        list(graph),
        {"source": {"physicalName": "managed_view", "columns": deepcopy(columns)}},
        grain,
    )


def test_standalone_storage_is_the_actual_source_not_a_copied_population():
    objects, managed, grain = model()
    binding = SourceStoragePolicy(objects, managed).binding(grain)
    assert binding["tableName"] == "Forecast"
    assert binding["keyColumns"] == {"product": "product"}
    assert binding["transactionId"] == "tx"


@pytest.mark.parametrize("transformation", ["pivot", "calendarResolution", "filters"])
def test_transformed_transactions_keep_the_existing_materialised_path(transformation):
    objects, managed, grain = model()
    next(obj for obj in objects if obj["kind"] == "transaction")["definition"][
        transformation
    ] = {"enabled": True}
    assert SourceStoragePolicy(objects, managed).binding(grain) is None


def test_pivoted_managed_source_is_not_eligible_even_with_effective_columns():
    objects, managed, grain = model()
    managed["source"]["columns"][1]["lineage"] = {"kind": "pivot"}
    assert SourceStoragePolicy(objects, managed).binding(grain) is None


def test_shared_source_does_not_silently_become_writable_from_one_transaction():
    objects, managed, grain = model()
    objects.append(
        {
            "kind": "transaction",
            "objectId": "other",
            "definition": {"sourceId": "source"},
        }
    )
    assert SourceStoragePolicy(objects, managed).binding(grain) is None


def test_disabled_pivot_is_still_an_untransformed_input():
    objects, managed, grain = model()
    next(obj for obj in objects if obj["kind"] == "transaction")["definition"][
        "pivot"
    ] = {"enabled": False}
    assert SourceStoragePolicy(objects, managed).binding(grain)


def test_source_snapshot_reads_owned_view_columns_without_generation_filter():
    artifact = {
        "artifactId": "view_artifact",
        "storageMode": "source",
        "keyColumns": {"product": "product"},
        "measureColumns": {"qty": "Quantity"},
        "columns": [
            {"columnId": "product", "name": "product", "nullable": False},
            {
                "columnId": "qty",
                "measureId": "qty",
                "name": "Quantity",
                "nullable": True,
            },
        ],
    }
    sql = snapshot_read('"db"."source_output"', artifact, "qty", ["product"]).query.sql(
        dialect="clickhouse"
    )
    assert "__semantic_generation" not in sql
    assert '"Quantity"' in sql and '"source_output"' in sql
