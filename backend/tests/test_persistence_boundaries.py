"""Enforce the service/persistence boundary and transaction-bound lookup contracts."""

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.repositories.metadata import metadata_repositories
from backend.app.repositories.metadata.criteria import (
    BindingLookup,
    ModelLookup,
    ObjectLookup,
    ReadLeaseLookup,
    RevisionLookup,
    RunLookup,
    UNSET,
)
from backend.app.services.base import now

APP = Path(__file__).resolve().parents[1] / "app"


def test_client_access_and_database_operators_stay_in_persistence_packages():
    violations = []
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP)
        if relative.parts[0] in {"repositories", "warehouse", "db"}:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = (
                    node.module
                    if isinstance(node, ast.ImportFrom)
                    else " ".join(alias.name for alias in node.names)
                )
                if (
                    imported
                    and imported != "pymongo.errors"
                    and any(
                        package in imported
                        for package in ["pymongo", "clickhouse_connect"]
                    )
                ):
                    violations.append((str(relative), node.lineno, "driver import"))
            if isinstance(node, ast.Dict):
                if any(
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value.startswith("$")
                    for key in node.keys
                ):
                    violations.append((str(relative), node.lineno, "database selector"))
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            receiver, method = ast.unparse(node.func.value), node.func.attr
            if method in {
                "get",
                "list",
                "insert",
                "replace",
                "delete",
                "delete_all",
            } and (
                receiver in {"repo", "repository", "self.repository"}
                or receiver.endswith(".store.repository")
            ):
                violations.append((str(relative), node.lineno, "raw document access"))
            if (
                method in {"command", "connect", "mongo_selector"}
                or (
                    method == "run"
                    and (receiver == "warehouse" or receiver.endswith(".warehouse"))
                )
                or (
                    method == "query"
                    and (
                        receiver == "client"
                        or receiver.endswith(".client")
                        or receiver == "connect()"
                    )
                )
            ):
                violations.append((str(relative), node.lineno, "client execution"))
    assert not violations, violations


def test_services_do_not_own_collection_names_or_sql_generation():
    from backend.app.db.schema import CollectionSchemas

    collections = set(CollectionSchemas.REQUIRED)
    violations = []
    for path in (APP / "services").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in collections or (
                    node.value.lstrip()
                    .upper()
                    .startswith(("SELECT ", "INSERT ", "ALTER ", "DROP ", "CREATE "))
                    and not node.value.endswith(".")
                ):
                    violations.append((path.name, node.lineno))
            if isinstance(node, ast.ImportFrom) and node.module == "sqlglot":
                violations.append((path.name, node.lineno))
    assert not violations, violations


def test_lookups_distinguish_missing_null_and_membership_and_reject_operator_injection():
    assert RunLookup().mongo_selector() == {}
    assert RunLookup(is_edit=None).mongo_selector() == {"isEdit": None}
    assert RunLookup(is_edit_in=(False, None)).mongo_selector() == {
        "isEdit": {"$in": [False, None]}
    }
    assert RunLookup(state="queued", lease_until_before=UNSET).mongo_selector() == {
        "state": "queued"
    }
    expiry = now()
    assert ReadLeaseLookup(artifact_id="a", expires_after=expiry).mongo_selector() == {
        "artifactId": "a",
        "expiresAt": {"$gt": expiry},
    }
    with pytest.raises(TypeError):
        ObjectLookup(object_id={"$ne": None}).mongo_selector()
    with pytest.raises(TypeError):
        RunLookup(state_in="running").mongo_selector()
    with pytest.raises(TypeError):
        ObjectLookup(unknown_field="value")
    lookup = ObjectLookup(object_id="stable")
    with pytest.raises(FrozenInstanceError):
        lookup.object_id = "changed"


@pytest.mark.asyncio
async def test_repositories_preserve_transaction_adapter_and_optimistic_conditions():
    documents = AsyncMock()
    documents.replace.return_value = False
    repositories = metadata_repositories(documents)
    revision = {"modelId": "m", "revisionId": "r", "version": 2}
    assert not await repositories.revisions.replace(
        RevisionLookup(
            model_id="m", revision_id="r", version=1, approval_status="draft"
        ),
        revision,
    )
    documents.replace.assert_awaited_once_with(
        "semantic_revisions",
        {"modelId": "m", "revisionId": "r", "version": 1, "approvalStatus": "draft"},
        revision,
    )
    documents.get.return_value = {"runId": "run"}
    assert await repositories.runs.find(
        RunLookup(run_id="run", fence=3, owner="worker")
    ) == {"runId": "run"}
    documents.get.assert_awaited_with(
        "engine_runs", {"runId": "run", "fence": 3, "owner": "worker"}
    )
    with pytest.raises(TypeError):
        await repositories.artifacts.find(ModelLookup(model_id="m"))


@pytest.mark.asyncio
async def test_identity_lookups_preserve_scoping_without_forwarding_arbitrary_fields():
    documents = AsyncMock()
    repositories = metadata_repositories(documents)
    identity = {
        "modelId": "m",
        "revisionId": "r",
        "resourceKind": "measureValues",
        "resourceId": "level",
    }
    await repositories.bindings.find(BindingLookup.from_record(identity))
    documents.get.assert_awaited_once_with("engine_artifact_bindings", identity)
    with pytest.raises(TypeError):
        BindingLookup.from_record({"artifactId": {"$ne": None}})


def test_repositories_reuse_bindings_without_sharing_transaction_sessions():
    from backend.app.repositories.mongo import MongoRepository
    from backend.app.repositories.metadata.records import RecordCategory

    database = object()
    first_session, second_session = object(), object()
    first, second = (
        MongoRepository(database, first_session),
        MongoRepository(database, second_session),
    )
    first_repositories = metadata_repositories(first)
    assert first_repositories is metadata_repositories(first)
    assert first_repositories.objects is metadata_repositories(first).objects
    assert first_repositories.activation is metadata_repositories(first).activation
    assert first_repositories.records(
        RecordCategory.EVIDENCE
    ) is first_repositories.records(RecordCategory.EVIDENCE)
    assert first_repositories.objects is not metadata_repositories(second).objects
    assert first_repositories.objects.documents.session is first_session
    assert metadata_repositories(second).objects.documents.session is second_session


@pytest.mark.asyncio
async def test_read_repository_lifetime_tracks_application_store_and_reopen():
    from types import SimpleNamespace
    from backend.app.core.config import Settings
    from backend.app.repositories.mongo import MongoMetadataStore

    store = MongoMetadataStore(Settings(_env_file=None))
    store.database = object()
    store.client = SimpleNamespace(close=AsyncMock())
    original = store.repository
    assert original is store.repository
    assert (
        metadata_repositories(original).runs
        is metadata_repositories(store.repository).runs
    )
    await store.close()
    with pytest.raises(RuntimeError):
        _ = store.repository
    store.database = object()
    assert store.repository is not original


def test_stateless_warehouse_query_adapter_is_reused():
    from backend.tests.fake_warehouse import FakeWarehouse

    warehouse = FakeWarehouse()
    assert warehouse.queries is warehouse.queries


def test_shared_dimension_compiler_does_not_keep_request_check_state():
    from backend.app.engine.dimension_compiler import DimensionCompiler
    from backend.app.engine.contracts import RelationScan

    compiler = DimensionCompiler("warehouse")
    scan = RelationScan("source", (("key", "source_key"),))
    first = compiler.render(scan)
    assert first == compiler.render(scan)
    assert not hasattr(compiler, "input_checks")
