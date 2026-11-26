# ruff: noqa: F811
"""Explicit modeller-owned contexts, independent of upload-default inference."""

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_decision_view_clickhouse import seed_view, query_view, field
from .test_measures_clickhouse import BASE, values, assert_post
from .test_incremental_clickhouse import run_incremental
from .test_measure_edits_clickhouse import editable, edit
from .test_managed_clickhouse import await_run
from .test_planning_clickhouse import build
from backend.tests.conftest import object_payload

pytestmark = pytest.mark.integration


@pytest.fixture
def authored_network(clickhouse_app):
    client, admin, database, settings = clickhouse_app
    # These tests explicitly author semantic definitions on existing warehouse
    # inputs. Automatic CSV onboarding has its own real-ClickHouse test suite.
    client.app.state.container.warehouse_sources.upload_metadata = None
    seed_view(client)
    return client, admin, database, settings


def test_native_targets_rollups_attributes_and_runtime_formulas(authored_network):
    client, _, _, _ = authored_network
    base, _ = values(client, "base")
    native, _ = values(client, "pt")
    assert {c["name"] for c in native["columns"] if c.get("measureId")} == {
        "FQTY",
        "SQTY",
        "AVGF",
        "ErrorAF",
        "ErrorCF",
    }
    lookup, _ = values(client, "brand_zone")
    assert lookup["generationId"] is None
    assert lookup["materialisation"] == []
    options = client.get(BASE + "/decision-view/options").json()
    assert "brand_zone" not in {dim["dimensionId"] for dim in options["dimensions"]}
    columns = [column for dim in options["dimensions"] for column in dim["columns"]]
    assert {"Brand", "Zone"} <= {column["name"] for column in columns}
    assert len([col for col in columns if col["name"].lower() == "product"]) == 1
    result = query_view(
        client,
        [field("Product", "brand")],
        [field("Location", "zone")],
        ["FQTY", "SQTY", "ErrorAF", "ErrorCF"],
    )
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == [
        ["A", "Z1", "110.0", "140.0", "30.0", "70.0"],
        ["A", "Z2", "30.0", "60.0", "30.0", "30.0"],
        ["B", "Z2", "-10.0", "20.0", "30.0", "30.0"],
    ]
    result = query_view(
        client,
        [field("Product", "brand")],
        measures=["FQTY", "SQTY", "ErrorAF", "ErrorCF", "AVGF"],
    )
    assert result.status_code == 200, result.text
    assert result.json()["rows"] == [
        ["A", "140.0", "200.0", "60.0", "100.0", "46.666667"],
        ["B", "-10.0", "20.0", "30.0", "30.0", "-10.0"],
    ]
    unchanged = run_incremental(client)
    assert unchanged["state"] == "succeeded", unchanged
    assert unchanged["reused"] is True
    assert values(client, "brand_zone")[0]["generationId"] is None


def test_edits_refresh_shared_native_representations(authored_network):
    client, _, _, _ = authored_network
    editable(client, "FQTY", "equal")
    run = await_run(client, BASE, edit(client, value=200))
    assert run["state"] == "succeeded", run
    response = query_view(
        client, [field("Product", "brand")], [field("Location", "zone")], ["FQTY"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0] == ["A", "Z1", "200.0"]
    native, columns = values(client, "pt")
    assert sum(float(row[columns["FQTY"]]) for row in native["rows"]) == 220
    assert values(client, "brand_zone")[0]["generationId"] is None
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    assert run["reused"] is True
    response = query_view(
        client, [field("Product", "brand")], [field("Location", "zone")], ["FQTY"]
    )
    assert response.json()["rows"][0] == ["A", "Z1", "200.0"]


def test_no_base_rollup_and_count_inputs_use_saved_native_values(authored_network):
    client, _, _, _ = authored_network
    parent = client.get(BASE + "/dimensions/pt").json()
    payload = object_payload(parent)
    payload["definition"]["memberBindings"] = [
        {
            "memberCatalogId": "Product",
            "keyMappings": [{"grainColumnId": "product", "memberColumnId": "product"}],
            "selectedAttributeColumnIds": ["brand"],
        }
    ]
    response = client.put(
        BASE + "/dimensions/pt",
        json=payload,
        headers={"If-Match": str(parent["version"])},
    )
    assert response.status_code == 200, response.text
    assert build(client, "pt", response.json()["version"])["state"] == "succeeded"
    for mid in ("ErrorAF", "ErrorCF"):
        document = client.get(BASE + "/measures/" + mid).json()
        response = client.delete(
            BASE + "/measures/" + mid, headers={"If-Match": str(document["version"])}
        )
        assert response.status_code == 204, response.text
    revision = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"baseDimensionId": None, "changeReason": "Independent native contexts"},
        headers={"If-Match": str(revision["version"])},
    )
    assert response.status_code == 200, response.text
    for mid, formula in [
        ("PriceDifference", "[FQTY] - [SQTY]"),
        ("Positive", "COUNT_IF([FQTY] > 0)"),
    ]:
        assert_post(
            client,
            "/measures",
            {
                "objectId": mid,
                "name": mid,
                "displayName": mid,
                "business": {},
                "definition": {
                    "measureType": "calculated",
                    "outputGrain": {"kind": "dimension", "objectId": "pt"},
                    "calculationType": "AggregateFirst",
                    "formula": {
                        "languageVersion": "planning-v1",
                        "displayText": formula,
                    },
                },
            },
        )
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    response = query_view(
        client,
        [field("Product", "brand")],
        measures=["FQTY", "SQTY", "PriceDifference", "Positive"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [
        ["A", "140.0", "200.0", "-60.0", "2.0"],
        ["B", "-10.0", "20.0", "-30.0", "0.0"],
    ]
    base, _ = values(client, "base")
    assert "SQTY" not in {col.get("measureId") for col in base["columns"]}


def test_incremental_upgrade_preserves_edits_and_removes_obsolete_bindings(
    authored_network,
):
    from backend.app.repositories.metadata import metadata_repositories
    from backend.app.repositories.metadata.criteria import ArtifactLookup, BindingLookup

    client, _, _, _ = authored_network
    editable(client, "FQTY", "equal")
    assert await_run(client, BASE, edit(client, value=200))["state"] == "succeeded"

    async def legacy_metadata():
        repositories = metadata_repositories(
            client.app.state.container.store.repository
        )
        bindings = await repositories.bindings.list(
            BindingLookup(
                model_id="default_model",
                revision_id="revision_1",
                resource_kind="measureValues",
            ),
            order_by="resourceId",
            limit=20,
        )
        for binding in bindings:
            artifact = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Values"
            )
            await repositories.artifacts.replace(
                ArtifactLookup(artifact_id=artifact["artifactId"]),
                {
                    **artifact,
                    "materialisationManifest": {
                        **artifact["materialisationManifest"],
                        "policy": "incremental-representations-v2-transaction-grains",
                    },
                },
            )
        # Old releases published bindings for every Dimension. A retired target
        # must no longer remain active when the new generation is committed.
        await repositories.bindings.insert({**bindings[0], "resourceId": "brand_zone"})

    client.portal.call(legacy_metadata)
    run = run_incremental(client)
    assert run["state"] == "succeeded", run
    response = query_view(
        client, [field("Product", "brand")], [field("Location", "zone")], ["FQTY"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0] == ["A", "Z1", "200.0"]
    assert values(client, "brand_zone")[0]["generationId"] is None
