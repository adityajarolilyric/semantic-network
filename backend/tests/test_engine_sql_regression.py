"""Stable SQL contracts for the canonical PLT/PT network during structural refactors."""

import hashlib
import json
from pathlib import Path

from backend.app.engine.graph.dependencies import ordered_dimensions
from backend.app.engine.populations.planner import DimensionPlanner
from backend.app.engine.populations.compiler import DimensionCompiler
from backend.app.engine.measures.compiler import MeasureCompiler
from backend.tests.conftest import MODEL, REVISION


def canonical_sql_contracts(client):
    service = client.app.state.container.transaction_resolution
    objects = client.portal.call(
        service.graph_objects, service.store.repository, MODEL, REVISION
    )
    managed = {
        obj["objectId"]: {
            "artifactId": "fixture_" + obj["objectId"],
            "physicalName": obj["definition"]["tableName"],
            "columns": obj["definition"]["columns"],
        }
        for obj in objects
        if obj["kind"] == "source"
    }
    relations, populations, contracts = dict(managed), {}, {}
    for dimension in ordered_dimensions(objects):
        plan = DimensionPlanner().plan(dimension, objects, relations)
        compiled = DimensionCompiler("fixture").compile(plan)
        key = dimension["objectId"]
        contracts["population:" + key] = plan_contract(compiled)
        populations[key] = {
            "artifactId": "fixture_" + key,
            "physicalName": key,
            "columns": list(plan.schema.columns),
        }
        relations[key] = populations[key]
    for key in ("pl_plt", "pl_pt"):
        compiler = MeasureCompiler("fixture", objects, populations, managed, "pl_plt")
        compiled, unavailable = compiler.compile_level(key)
        contracts["measures:" + key] = {
            **plan_contract(compiled),
            "unavailable": unavailable,
        }
    return {
        key: hashlib.sha256(
            json.dumps(value, sort_keys=True, default=str).encode()
        ).hexdigest()
        for key, value in contracts.items()
    }


def plan_contract(compiled):
    return {
        "sql": compiled.sql,
        "checks": [(check.code, check.sql) for check in compiled.checks],
        "columns": compiled.schema.columns,
        "version": compiled.compiler_version,
    }


def test_canonical_sql_is_unchanged(populated):
    expected = json.loads(
        (Path(__file__).parent / "fixtures/engine_sql_contracts.json").read_text()
    )
    assert canonical_sql_contracts(populated) == expected
