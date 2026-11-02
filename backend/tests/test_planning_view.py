from datetime import timedelta
import pytest
from pydantic import ValidationError

from backend.app.engine.artifacts import ArtifactCollector
from backend.app.engine.planning_view import (
    PlanningViewPlanner,
    ViewRelationships,
    TARGET,
)
from backend.app.schemas.planning_view import PlanningViewRequest
from backend.app.services.base import now
from backend.tests.test_engine import context, submit, SPEC


def test_view_axes_are_distinct_and_bounded():
    field = {"dimensionId": "product", "columnId": "brand"}
    with pytest.raises(ValidationError):
        PlanningViewRequest.model_validate(
            {"rows": [field], "columns": [field], "measures": ["q"]}
        )
    with pytest.raises(ValidationError):
        PlanningViewRequest.model_validate({"measures": ["q", "q"]})
    with pytest.raises(ValidationError):
        PlanningViewRequest.model_validate({"measures": []})
    assert PlanningViewRequest.model_validate({"measures": ["q"]}).rows == []


def test_virtual_grain_resolves_formula_grain_references():
    obj = {
        "objectId": "Product",
        "kind": "dimension",
        "name": "Product",
        "isTimeDim": False,
        "definition": {
            "sourceId": "products",
            "keyColumnIds": ["product"],
            "attributeColumnIds": ["product"],
            "columns": [
                {
                    "columnId": "product",
                    "sourceColumnId": "col_1",
                    "displayName": "Product ID",
                }
            ],
        },
    }
    request = PlanningViewRequest.model_validate(
        {"rows": [{"dimensionId": "Product", "columnId": "product"}], "measures": ["q"]}
    )
    fields = PlanningViewPlanner([obj], "base").fields(request)
    relation = ViewRelationships([obj], fields)
    assert relation.resolve(TARGET, fields[0]["coordinate"]).column_id == "axis_0"


@pytest.mark.asyncio
async def test_read_lease_prevents_collection_after_generation_is_unbound():
    store, service, worker = await context()
    await submit(service)
    await worker.execute(await worker.claim())
    await submit(service, SPEC, 1, "second")
    await worker.execute(await worker.claim())
    artifacts = store.documents["engine_artifacts"]
    unbound = [
        a
        for a in artifacts
        if not await store.repository.get(
            "engine_artifact_bindings", {"artifactId": a["artifactId"]}
        )
    ]
    old = unbound[0]
    for artifact in artifacts:
        artifact["createdAt"] = now() - timedelta(days=2)
    await store.repository.insert(
        "engine_read_leases",
        {
            "readId": "read_pivot",
            "artifactId": old["artifactId"],
            "expiresAt": now() + timedelta(minutes=3),
        },
    )

    class Warehouse:
        dropped = []

        async def run(self, fn):
            return fn(lambda: self)

        def table_info(self, client, name):
            artifact = next(a for a in artifacts if a["physicalName"] == name)
            return "View", "semanticArtifact:" + artifact["artifactId"]

        def qualify(self, name):
            return name

        def command(self, sql):
            self.dropped.append(sql)

    service.executor.warehouse = Warehouse()
    collector = ArtifactCollector(service)
    await collector.collect()
    assert not service.executor.warehouse.dropped
    await store.repository.delete("engine_read_leases", {"readId": "read_pivot"})
    await collector.collect()
    assert len(service.executor.warehouse.dropped) == 1
