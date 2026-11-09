from backend.app.engine.execution.storage.view_names import (
    source_view_name,
    source_view_role,
)


def source(kind="String"):
    return {
        "objectId": "s",
        "kind": "source",
        "definition": {
            "tableName": "Forecast / daily",
            "columns": [{"physicalType": kind}],
        },
    }


def test_names_include_role_actual_source_name_and_unique_artifact_identity():
    assert (
        source_view_name(source(), "artifact_1234", "dim") == "dim_Forecast_daily_1234"
    )
    assert (
        source_view_name(source(), "artifact_5678", "trans")
        == "trans_Forecast_daily_5678"
    )
    long = source()
    long["definition"]["tableName"] = "x" * 1000
    assert len(source_view_name(long, "artifact_" + "a" * 32, "trans")) < 128


def test_explicit_semantic_use_precedes_numeric_onboarding_fallback():
    transaction = {"kind": "transaction", "definition": {"sourceId": "s"}}
    dimension = {
        "kind": "dimension",
        "definition": {"population": {"operation": "source", "sourceId": "s"}},
    }
    assert source_view_role(source(), []) == "dim"
    assert source_view_role(source("Float64"), []) == "trans"
    assert source_view_role(source("UInt32"), [dimension]) == "dim"
    assert source_view_role(source(), [transaction]) == "trans"
    assert source_view_role(source(), [transaction, dimension]) == "trans"
