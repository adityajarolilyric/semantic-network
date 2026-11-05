"""Safety contracts for shared infrastructure extracted from production paths."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.app.core.errors import Conflict, MetadataError
from backend.app.engine.captured_inputs import CapturedRunInputs
from backend.app.engine.checks import CheckDiagnostics, WarehouseCheckRunner
from backend.app.engine.compilation_context import CompilationContext
from backend.app.engine.contracts import Check
from backend.app.engine.read_leases import ArtifactReadLeases
from backend.app.services.base import now
from backend.tests.memory_store import MemoryStore
from backend.tests.test_engine import SOURCE, context, submit


@pytest.mark.asyncio
async def test_read_acquisition_and_exception_cleanup_keep_retention_balanced():
    store = MemoryStore()
    artifact = {"artifactId": "artifact", "state": "ready", "pinVersion": 7}
    await store.repository.insert("engine_artifacts", artifact)
    leases = ArtifactReadLeases(store)
    await store.transaction(
        lambda repository: leases.acquire(
            repository,
            "read",
            [artifact, artifact],
            resource="Input",
            retired_message="Retired",
        )
    )
    assert (await store.repository.get("engine_artifacts", {"artifactId": "artifact"}))[
        "pinVersion"
    ] == 8
    with pytest.raises(ValueError, match="warehouse failed"):
        async with leases.hold("read", ["artifact"], "Expired"):
            raise ValueError("warehouse failed")
    assert await store.repository.get("engine_read_leases", {"readId": "read"}) is None


@pytest.mark.asyncio
async def test_read_lease_cannot_be_resurrected_after_expiration_or_retirement():
    store = MemoryStore()
    leases = ArtifactReadLeases(store)
    await store.repository.insert(
        "engine_read_leases",
        {
            "readId": "read",
            "artifactId": "artifact",
            "expiresAt": now() - timedelta(seconds=1),
        },
    )
    with pytest.raises(Conflict, match="Expired"):
        await leases.verify(store.repository, "read", ["artifact"], "Expired")
    artifact = {"artifactId": "retired", "state": "retiring"}
    await store.repository.insert("engine_artifacts", artifact)
    with pytest.raises(Conflict, match="Retired"):
        await store.transaction(
            lambda repository: leases.acquire(
                repository,
                "new",
                [artifact],
                resource="Input",
                retired_message="Retired",
            )
        )
    assert await store.repository.get("engine_read_leases", {"readId": "new"}) is None


@pytest.mark.asyncio
async def test_lease_renewal_extends_live_reads_and_stops_when_cancelled():
    store = MemoryStore()
    leases = ArtifactReadLeases(store)
    leases.renewal_interval_seconds = 0.001
    previous_expiry = now() + timedelta(seconds=1)
    await store.repository.insert(
        "engine_read_leases",
        {
            "readId": "read",
            "artifactId": "artifact",
            "expiresAt": previous_expiry,
        },
    )
    async with leases.hold("read", ["artifact"], "Expired") as heartbeat:
        for _ in range(100):
            await asyncio.sleep(0.001)
            lease = await store.repository.get("engine_read_leases", {"readId": "read"})
            if lease["expiresAt"] > previous_expiry:
                break
        assert lease["expiresAt"] > previous_expiry
    assert heartbeat.done()
    assert await store.repository.get("engine_read_leases", {"readId": "read"}) is None


def test_captured_inputs_preserve_legacy_layouts_and_opaque_ids():
    documents = [
        ("source", {"objectId": "source"}),
        ("__population_level__one", {"artifactId": "population"}),
        (
            "__managed_source",
            {"artifact": {"artifactId": "managed"}, "definition": {"version": 2}},
        ),
        ("__values_level__one", {"artifactId": "values"}),
        ("__previous", {"generationId": "old"}),
        ("__manifest_level__one", {"policy": "current"}),
        (
            "__relation_source",
            {
                "resourceId": "source",
                "relation": {"artifactId": "managed"},
                "managed": {"version": 2},
            },
        ),
    ]
    captured = CapturedRunInputs(
        tuple({"inputId": key, "document": value} for key, value in documents)
    )
    assert captured.objects == [{"objectId": "source"}]
    assert captured.populations["level__one"]["artifactId"] == "population"
    assert captured.managed_artifacts["source"] == captured.relations["source"]
    assert (
        captured.managed_definitions["source"]
        == captured.relation_definitions["source"]
    )
    assert captured.value_snapshots["level__one"]["artifactId"] == "values"
    assert captured.materialisation_manifests["level__one"] == {"policy": "current"}
    assert captured.single("__previous") == {"generationId": "old"}
    assert captured.single("missing") is None


def test_compilation_context_is_measure_independent_and_keeps_query_limits():
    compiler = CompilationContext("warehouse", [SOURCE], {})
    first = compiler.cte("SELECT 1")
    assert first == "measure_node_0"  # Existing SQL/plan fingerprints stay stable.
    assert (
        compiler.with_sql('SELECT * FROM "measure_node_0"')
        == 'WITH `measure_node_0` AS (SELECT 1) SELECT * FROM "measure_node_0"'
    )
    compiler.max_nodes = 1
    with pytest.raises(MetadataError) as error:
        compiler.cte("SELECT 2")
    assert error.value.code == "measure_graph_limit"


@pytest.mark.asyncio
async def test_check_runner_preserves_error_codes_and_bounded_details():
    class Warehouse:
        cell = staticmethod(str)

        async def run(self, operation):
            return operation(
                lambda: SimpleNamespace(
                    query=lambda *args, **kwargs: SimpleNamespace(
                        result_rows=[["x" * 30]] * 30
                    )
                )
            )

    with pytest.raises(MetadataError) as error:
        await WarehouseCheckRunner(Warehouse()).run(
            [Check("bad", "SELECT 1")],
            {},
            CheckDiagnostics(
                {"bad": "Original diagnostic"},
                "Fallback",
                row_limit=2,
                detail_length=10,
            ),
        )
    assert error.value.code == "bad"
    assert error.value.message == "Original diagnostic"
    assert len(error.value.details) == 2
    assert all(len(item["message"]) == 10 for item in error.value.details)


@pytest.mark.asyncio
async def test_worker_rejects_unknown_handlers_without_running_a_managed_build():
    store, service, worker = await context()
    run = await submit(service)
    stored = await store.repository.get("engine_runs", {"runId": run["runId"]})
    await store.repository.replace(
        "engine_runs", {"runId": run["runId"]}, {**stored, "resourceKind": "unknown"}
    )
    await worker.execute(await worker.claim())
    failed = await store.repository.get("engine_runs", {"runId": run["runId"]})
    assert failed["state"] == "failed"
    assert failed["error"]["code"] == "unsupported_engine_target"
    assert not await store.repository.get("engine_artifact_bindings", {})


@pytest.mark.asyncio
async def test_managed_source_capture_requires_current_ready_view_and_preserves_schema():
    from backend.app.engine.managed_inputs import ManagedInputCapture

    store = MemoryStore()
    source = {
        **SOURCE,
        "version": 3,
        "definition": {**SOURCE["definition"], "inScope": True},
    }
    identity = {
        "modelId": "model",
        "revisionId": "revision",
        "sourceId": source["objectId"],
    }
    definition = {
        **identity,
        "definitionHash": "current",
        "definition": {"columns": [{"columnId": "stable", "name": "Pivoted"}]},
    }
    binding = {**identity, "artifactId": "artifact", "definitionHash": "stale"}
    artifact = {
        "artifactId": "artifact",
        "state": "candidate",
        "physicalName": "managed_view",
    }
    for collection, document in [
        ("semantic_managed_tables", definition),
        ("engine_artifact_bindings", binding),
        ("engine_artifacts", artifact),
    ]:
        await store.repository.insert(collection, document)
    capture = ManagedInputCapture()
    with pytest.raises(Conflict, match="Build the input managed view first"):
        await capture.capture_source(store.repository, "model", "revision", source)
    await store.repository.replace(
        "engine_artifact_bindings", identity, {**binding, "definitionHash": "current"}
    )
    with pytest.raises(Conflict, match="not ready"):
        await capture.capture_source(store.repository, "model", "revision", source)
    await store.repository.replace(
        "engine_artifacts", {"artifactId": "artifact"}, {**artifact, "state": "ready"}
    )
    snapshot = await capture.capture_source(
        store.repository, "model", "revision", source
    )
    assert snapshot.source_version == 3
    assert snapshot.artifact["physicalName"] == "managed_view"
    assert snapshot.artifact["columns"] == definition["definition"]["columns"]
    assert snapshot.definition == definition
