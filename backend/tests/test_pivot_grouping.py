from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlglot import exp

from backend.app.core.errors import MetadataError
from backend.app.engine.transformations.pivot.compiler import ClickHouseCompiler
from backend.app.engine.transformations.pivot.planner import ManagedTablePlanner
from backend.app.schemas.managed_tables import ManagedDefinition
from backend.tests.test_engine import SOURCE, SPEC, context, submit
from backend.tests.test_pivot_modes import row_source, row_spec


def grouped_spec():
    spec = row_spec()
    spec["pipeline"][0].pop("mode")
    spec["pipeline"][0]["recordKeyColumnIds"] = []
    return spec


def test_group_values_uses_retained_and_both_pair_discriminators_and_values():
    plan = ManagedTablePlanner().plan(
        row_source(), ManagedDefinition.model_validate(grouped_spec())
    )
    compiled = ClickHouseCompiler("semantic").compile(plan)
    grouped = compiled.query.args["group"]
    assert [c.name for c in grouped.find_all(exp.Column)] == [
        "week",
        "qty",
        "pa",
        "pv",
        "la",
        "lv",
    ]
    assert [c["name"] for c in plan.schema.columns] == ["week", "qty", "brand", "zone"]
    assert not compiled.checks
    assert "mode" not in plan.definition["pipeline"][0]
    assert "resolvedMode" not in plan.definition["pipeline"][0]


def test_record_keys_are_retained_inputs_and_cannot_be_pair_inputs():
    spec = grouped_spec()
    spec["pipeline"][0]["recordKeyColumnIds"] = ["pv"]
    with pytest.raises(MetadataError, match="Record keys"):
        ManagedTablePlanner().plan(row_source(), ManagedDefinition.model_validate(spec))
    spec["pipeline"][0]["recordKeyColumnIds"] = ["week", "week"]
    with pytest.raises(MetadataError, match="Record keys"):
        ManagedTablePlanner().plan(row_source(), ManagedDefinition.model_validate(spec))


def test_grouping_and_output_key_changes_do_not_change_output_ids():
    spec = grouped_spec()
    first = ManagedTablePlanner().plan(
        row_source(), ManagedDefinition.model_validate(spec)
    )
    spec["pipeline"][0]["recordKeyColumnIds"] = ["week"]
    second = ManagedTablePlanner().plan(
        row_source(), ManagedDefinition.model_validate(spec)
    )
    assert [c["columnId"] for c in first.schema.columns] == [
        c["columnId"] for c in second.schema.columns
    ]
    checks = ClickHouseCompiler("semantic").compile(second).checks
    assert "conflicting_retained_values" in [c.code for c in checks]
    assert len([c for c in checks if c.code == "duplicate_pivot_cell"]) == 2


@pytest.mark.asyncio
async def test_schema_analysis_never_profiles_data_or_submits_a_job():
    store, service, _ = await context()
    spec = deepcopy(SPEC)
    spec["pipeline"][0]["recordKeyColumnIds"] = ["product"]
    service.executor.inspect = AsyncMock(
        side_effect=AssertionError("schema analysis must not read warehouse")
    )
    result = await service.schema(
        "default_model", "revision_1", "raw", ManagedDefinition.model_validate(spec)
    )
    assert [c["name"] for c in result["columns"]] == ["product", "FQTY"]
    service.executor.inspect.assert_not_called()
    assert await store.repository.list("engine_runs", {}, key="runId", limit=100) == []


@pytest.mark.asyncio
async def test_grouped_request_idempotency_replays_the_same_run():
    _, service, worker = await context()
    spec = deepcopy(SPEC)
    spec["pipeline"][0]["recordKeyColumnIds"] = ["product"]
    first = await submit(service, spec)
    await worker.execute(await worker.claim())
    replay = await submit(service, spec)
    assert replay["runId"] == first["runId"]
    assert replay["state"] == "succeeded"


def test_legacy_frozen_auto_compiles_without_detection_and_keeps_serialization():
    spec = deepcopy(SPEC)
    spec["pipeline"][0].update(mode="auto", resolvedMode="merge_rows")
    plan = ManagedTablePlanner().plan(SOURCE, ManagedDefinition.model_validate(spec))
    assert "recordKeyColumnIds" not in plan.definition["pipeline"][0]
    old = ManagedTablePlanner().plan(SOURCE, ManagedDefinition.model_validate(SPEC))
    assert (
        ClickHouseCompiler("semantic").compile(plan).sql
        == ClickHouseCompiler("semantic").compile(old).sql
    )
    spec["pipeline"][0].pop("resolvedMode")
    with pytest.raises(MetadataError, match="saved resolvedMode"):
        ManagedTablePlanner().plan(SOURCE, ManagedDefinition.model_validate(spec))
    spec["pipeline"][0]["recordKeyColumnIds"] = []
    with pytest.raises(ValidationError, match="legacy"):
        ManagedDefinition.model_validate(spec)
