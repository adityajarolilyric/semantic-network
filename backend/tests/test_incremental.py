from copy import deepcopy

from backend.app.engine.incremental import MaterialisationManifest


def model():
    sources = [
        {
            "objectId": sid,
            "kind": "source",
            "definition": {"inScope": True, "tableName": sid, "columns": []},
        }
        for sid in ("population", "forecast", "sales")
    ]
    level = {
        "objectId": "base",
        "kind": "dimension",
        "name": "Base",
        "definition": {
            "population": {"operation": "source", "sourceId": "population"},
            "grain": {
                "keyIds": ["product", "location"],
                "columns": [
                    {"grainColumnId": k, "name": k, "populationRef": {"columnId": k}}
                    for k in ["product", "location"]
                ],
            },
            "memberBindings": [],
        },
    }
    transactions = [
        {
            "objectId": "tx_" + sid,
            "kind": "transaction",
            "definition": {
                "sourceId": sid,
                "dimensionId": "base",
                "mappingMode": "direct",
                "keyMappings": [
                    {"sourceColumnId": k, "grainColumnId": k}
                    for k in ["product", "location"]
                ],
            },
        }
        for sid in ["forecast", "sales"]
    ]
    measures = [
        {
            "objectId": mid,
            "name": mid,
            "kind": "measure",
            "definition": {
                "measureType": "referenced",
                "nativeDimensionId": "base",
                "transactionId": "tx_" + sid,
                "valueColumnId": "qty",
                "resultType": {"type": "number", "nullable": True},
                "editable": True,
                "aggregation": {"nonTime": "sum", "time": "sum"},
                "allocation": {"mode": "equal"},
            },
        }
        for mid, sid in [("FQTY", "forecast"), ("SQTY", "sales")]
    ]
    calc = {
        "objectId": "Error",
        "name": "Error",
        "kind": "measure",
        "definition": {
            "measureType": "calculated",
            "nativeDimensionId": "base",
            "resultType": {"type": "number", "nullable": True},
            "calculationType": "AggregateFirst",
            "formula": {
                "expression": {
                    "type": "binary",
                    "operator": "subtract",
                    "left": {"type": "measureRef", "measureId": "FQTY"},
                    "right": {"type": "measureRef", "measureId": "SQTY"},
                }
            },
        },
    }
    return (
        sources + [level] + transactions + measures + [calc],
        {"base": {"artifactId": "population_1"}},
        {s: {"artifactId": s + "_1"} for s in ["population", "forecast", "sales"]},
    )


def manifest(objects, populations, managed):
    return MaterialisationManifest(objects, populations, managed, "base").all()


def test_new_measure_and_unrelated_new_level_preserve_existing_representation_fingerprints():
    objects, pops, managed = model()
    before = manifest(objects, pops, managed)["base"]["evaluations"]
    extra = deepcopy(next(o for o in objects if o["objectId"] == "FQTY"))
    extra.update(objectId="Extra", name="Extra")
    objects.append(extra)
    parent = deepcopy(next(o for o in objects if o["objectId"] == "base"))
    parent.update(objectId="pt", name="PT")
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
    pops["pt"] = {"artifactId": "pt_1"}
    after = manifest(objects, pops, managed)
    assert all(after["base"]["evaluations"][mid] == fp for mid, fp in before.items())
    assert "Extra" in after["base"]["evaluations"]
    assert "pt" not in after  # Unused Dimensions do not receive values tables.


def test_source_and_definition_changes_invalidate_only_dependent_measures():
    objects, pops, managed = model()
    before = manifest(objects, pops, managed)["base"]["evaluations"]
    managed["forecast"]["artifactId"] = "forecast_2"
    after = manifest(objects, pops, managed)["base"]["evaluations"]
    assert after["SQTY"] == before["SQTY"]
    assert after["FQTY"] != before["FQTY"] and after["Error"] != before["Error"]
    managed["forecast"]["artifactId"] = "forecast_1"
    next(o for o in objects if o["objectId"] == "FQTY")["definition"]["aggregation"][
        "nonTime"
    ] = "avg"
    changed = manifest(objects, pops, managed)["base"]["evaluations"]
    assert changed["SQTY"] == before["SQTY"]
    assert changed["FQTY"] != before["FQTY"] and changed["Error"] != before["Error"]


def test_presentation_and_edit_policy_changes_do_not_recompute_values():
    objects, pops, managed = model()
    before = manifest(objects, pops, managed)["base"]
    qty = next(o for o in objects if o["objectId"] == "FQTY")
    qty["name"] = "Forecast Qty"
    qty["definition"]["editable"] = False
    qty["definition"]["allocation"] = {"mode": "weighted_by_self"}
    after = manifest(objects, pops, managed)["base"]
    assert after["evaluations"] == before["evaluations"]
    assert after["layoutHash"] != before["layoutHash"]


def test_incremental_jobs_reject_runtime_policy_changes_without_falling_back_to_reload():
    import pytest
    from backend.app.core.errors import Conflict
    from backend.app.services.incremental_builds import IncrementalBuildCoordinator
    from backend.app.engine.incremental import INCREMENTAL_POLICY
    from backend.app.engine.measure_compiler import MEASURE_COMPILER_VERSION

    job = {
        "buildMode": "incremental",
        "incrementalPolicy": INCREMENTAL_POLICY,
        "incrementalCompilerVersion": MEASURE_COMPILER_VERSION,
    }
    IncrementalBuildCoordinator.validate_runtime_policy(job)
    with pytest.raises(Conflict):
        IncrementalBuildCoordinator.validate_runtime_policy(
            {**job, "incrementalCompilerVersion": "old"}
        )
    with pytest.raises(Conflict):
        IncrementalBuildCoordinator.validate_runtime_policy(
            {**job, "incrementalPolicy": "old"}
        )
    for mode in ("full", "edit"):
        IncrementalBuildCoordinator.validate_runtime_policy({**job, "buildMode": mode})
        with pytest.raises(Conflict):
            IncrementalBuildCoordinator.validate_runtime_policy({"buildMode": mode})
