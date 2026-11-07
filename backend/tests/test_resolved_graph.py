from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from backend.app.engine.capture_session import PopulationCaptureSession
from backend.app.engine.compilation_context import CompilationContext
from backend.app.engine.grain_contexts import resolve_grain_contexts
from backend.app.engine.managed_inputs import ManagedSourceSnapshot
from backend.app.engine.resolver import EffectiveSchemaResolver
from backend.app.engine.relationships import LevelRelationshipAnalyzer
from backend.app.services.dimension_builds import DimensionBuildService
from .test_incremental import model


def test_resolved_snapshot_is_detached_indexed_and_reused_only_when_unchanged():
    objects, _, _ = model()
    graph = resolve_grain_contexts(objects)
    assert resolve_grain_contexts(graph) is graph
    context = CompilationContext("warehouse", graph, {})
    analyzer = LevelRelationshipAnalyzer(graph)
    assert context.graph is graph
    assert context.objects["base"] is analyzer.objects["base"] is graph.by_id["base"]
    assert graph.by_kind["measure"]["FQTY"] is graph.by_id["FQTY"]
    with pytest.raises(TypeError):
        graph.by_id["new"] = {}
    original = objects[3]["name"]
    edited = deepcopy(graph)
    next(obj for obj in edited if obj["objectId"] == "base")["name"] = "Changed"
    assert graph.by_id["base"]["name"] == original
    assert resolve_grain_contexts(edited).by_id["base"]["name"] == "Changed"
    assert "nativeGrainId" not in objects[4]["definition"]


@pytest.mark.asyncio
async def test_effective_snapshot_does_not_reload_but_explicit_schema_change_reapplies():
    objects, _, _ = model()
    graph = EffectiveSchemaResolver.apply(objects, [])
    assert await EffectiveSchemaResolver.load(None, "model", "revision", graph) is graph
    changed = EffectiveSchemaResolver.apply(
        graph,
        [
            {
                "sourceId": "forecast",
                "definition": {
                    "columns": [{"columnId": "new", "name": "renamed"}],
                    "primaryKeyColumnIds": ["new"],
                },
            }
        ],
    )
    assert changed.by_id["forecast"]["definition"]["columns"][0]["name"] == "renamed"
    assert graph.by_id["forecast"]["definition"]["columns"] == []


@pytest.mark.asyncio
async def test_source_capture_is_memoised_within_operation_and_fresh_next_time():
    objects, _, _ = model()
    for obj in objects:
        obj["version"] = 1
    base = next(obj for obj in objects if obj["objectId"] == "base")
    sibling = {**deepcopy(base), "objectId": "sibling"}
    objects.append(sibling)
    service = DimensionBuildService.__new__(DimensionBuildService)
    snapshot = ManagedSourceSnapshot(
        {"artifactId": "first"},
        {"definitionHash": "schema1"},
        {"artifactId": "first"},
        1,
    )
    service.input_capture = AsyncMock()
    service.input_capture.capture_source.return_value = snapshot
    session = PopulationCaptureSession({obj["objectId"]: obj for obj in objects})
    first = await service.capture_population_inputs(
        None, "m", "r", base, objects, session=session
    )
    again = await service.capture_population_inputs(
        None, "m", "r", base, objects, session=session
    )
    await service.capture_population_inputs(
        None, "m", "r", sibling, objects, session=session
    )
    assert again is first
    assert service.input_capture.capture_source.await_count == 1
    service.input_capture.capture_source.return_value = ManagedSourceSnapshot(
        {"artifactId": "second"},
        {"definitionHash": "schema1"},
        {"artifactId": "second"},
        1,
    )
    fresh = await service.capture_population_inputs(None, "m", "r", base, objects)
    assert fresh["inputHash"] != first["inputHash"]
    assert service.input_capture.capture_source.await_count == 2


@pytest.mark.parametrize(
    "base,projected,expected",
    [
        (
            "base",
            False,
            {
                "base": "2f6d5eb7ef9d9388e639d98f96495932eb05feee5365e3b24957d8661ab7b6ad"
            },
        ),
        (
            "base",
            True,
            {
                "base": "cca50f3cb59d03b0761b888b438e6a1a54d606b27a5569b0f7669bb20c245071",
                "product": "280babc50178d2a2638453d502fc30b5719f364b7381235cb3df744324102ca2",
            },
        ),
        (
            None,
            True,
            {
                "base": "60f092b2eeb1f12c671818da0adef9f5d4c9ddbe934f647e4a14051e176dfcf6",
                "product": "43b770244775edc16ec883137d15c098ceeb3850c53b1f4206871fb1ea85b1ac",
            },
        ),
    ],
)
def test_saved_manifest_layout_hashes_remain_compatible(base, projected, expected, monkeypatch):
    # Captured from the preceding production manifest implementation. A structural
    # refactor must not invalidate manual edits by changing these fingerprints.
    from backend.app.engine.incremental import MaterialisationManifest
    monkeypatch.setattr("backend.app.engine.incremental.STORAGE_POLICY", "stable-row-generations-v1")
    from .test_representation_targets import with_projection

    objects, populations, managed = model()
    if projected:
        with_projection(objects, "product")
        transaction = next(obj for obj in objects if obj["objectId"] == "tx_sales")
        transaction["definition"]["dimensionId"] = "product"
        transaction["definition"]["keyMappings"] = [
            {"sourceColumnId": "product", "grainColumnId": "product"}
        ]
        populations["product"] = {"artifactId": "product_1"}
    manifests = MaterialisationManifest(
        resolve_grain_contexts(objects), populations, managed, base
    ).all()
    assert {key: value["layoutHash"] for key, value in manifests.items()} == expected
