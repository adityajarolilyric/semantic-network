import pytest

from .conftest import EXAMPLE, MODEL, ROOT, object_payload


def definition(object_id):
    return object_payload(
        next(o for o in EXAMPLE["semantic_objects"] if o["objectId"] == object_id)
    )


def etag(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return response.headers["etag"]


def publish(client):
    model = client.get(f"/api/v1/models/{MODEL}").json()
    return client.post(
        ROOT + "/publish",
        headers={"If-Match": etag(client, ROOT)},
        json={"expectedModelVersion": model["version"]},
    )


def test_full_example_and_publication(populated):
    client = populated
    report = client.post(ROOT + "/validate")
    assert report.json() == {"valid": True, "diagnostics": []}
    response = publish(client)
    assert response.status_code == 200, response.text
    assert response.json()["approvalStatus"] == "published"
    model = client.get(f"/api/v1/models/{MODEL}").json()
    assert model["publishedRevisionId"] == "rev_1"
    assert model["draftRevisionId"] is None
    assert client.get(ROOT).json()["baseDimensionId"] == "pl_plt"
    level = client.get(ROOT + "/dimensions/pl_plt").json()
    assert "isBase" not in level


def test_optimistic_update_and_request_id(populated):
    path = ROOT + "/dimension-members/dim_product"
    original = populated.get(path)
    payload = object_payload(original.json())
    payload["definition"]["columns"][0]["displayName"] = "SKU"
    updated = populated.put(
        path, json=payload, headers={"If-Match": original.headers["etag"]}
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.headers["etag"] == '"2"'
    assert (
        populated.put(
            path, json=payload, headers={"If-Match": original.headers["etag"]}
        ).status_code
        == 409
    )
    assert updated.headers["x-request-id"]


@pytest.mark.parametrize(
    "value,status",
    [(None, 428), ('W/"1"', 400), ("*", 400), ("0", 400), ("-1", 400), ("1,2", 400)],
)
def test_if_match_preconditions(populated, value, status):
    headers = {} if value is None else {"If-Match": value}
    response = populated.put(
        ROOT + "/dimension-members/dim_product",
        json=definition("dim_product"),
        headers=headers,
    )
    assert response.status_code == status


@pytest.mark.parametrize(
    "object_id,resource,field,value",
    [
        ("src_product", "sources", "displayName", "Products"),
        ("src_product", "sources", "tenantId", "tenant_1"),
        ("src_product", "sources", "physicalBinding", {}),
        ("pl_plt", "dimensions", "isBase", True),
    ],
)
def test_rejected_legacy_metadata_fields(populated, object_id, resource, field, value):
    payload = definition(object_id)
    payload[field] = value
    assert (
        populated.put(
            f"{ROOT}/{resource}/{object_id}", json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 422
    )


def test_source_column_display_name_rejected(populated):
    payload = definition("src_product")
    payload["definition"]["columns"][0]["displayName"] = "SKU"
    assert (
        populated.put(
            ROOT + "/sources/src_product", json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 422
    )


def test_source_physical_schema_is_read_only(populated):
    payload = definition("src_product")
    payload["definition"]["columns"][0]["name"] = "renamed_product"
    response = populated.put(
        ROOT + "/sources/src_product", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 409
    assert (
        populated.get(ROOT + "/sources/src_product").json()["definition"]["columns"][0][
            "name"
        ]
        == "product_id"
    )


def test_invalid_update_rolls_back_revision_and_object(populated):
    before = populated.get(ROOT).json()
    payload = definition("dim_product")
    payload["definition"]["sourceId"] = "missing"
    response = populated.put(
        ROOT + "/dimension-members/dim_product", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422
    assert populated.get(ROOT).json()["version"] == before["version"]
    assert populated.get(ROOT + "/dimension-members/dim_product").json()["version"] == 1


def test_referenced_object_delete_is_blocked(populated):
    response = populated.delete(
        ROOT + "/sources/src_product", headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422
    assert populated.get(ROOT + "/sources/src_product").status_code == 200


def test_selected_base_delete_is_blocked(populated):
    assert (
        populated.delete(
            ROOT + "/dimensions/pl_plt", headers={"If-Match": '"1"'}
        ).status_code
        == 409
    )


def test_published_definitions_immutable_and_clone_is_independent(populated):
    assert publish(populated).status_code == 200
    response = populated.put(
        ROOT + "/dimension-members/dim_product",
        json=definition("dim_product"),
        headers={"If-Match": '"1"'},
    )
    assert response.status_code == 409
    model = populated.get(f"/api/v1/models/{MODEL}").json()
    response = populated.post(
        f"/api/v1/models/{MODEL}/revisions",
        json={
            "revisionId": "rev_2",
            "expectedModelVersion": model["version"],
            "parentRevisionId": "rev_1",
        },
    )
    assert response.status_code == 201, response.text
    root2 = ROOT.replace("rev_1", "rev_2")
    assert response.json()["baseDimensionId"] == "pl_plt"
    payload = definition("dim_product")
    payload["displayName"] = "Products v2"
    assert (
        populated.put(
            root2 + "/dimension-members/dim_product", json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        populated.get(ROOT + "/dimension-members/dim_product").json()["displayName"]
        == "Product"
    )


def test_pagination_and_context_isolation(populated):
    first = populated.get(ROOT + "/sources?limit=2").json()
    second = populated.get(
        ROOT + "/sources", params={"limit": 2, "cursor": first["nextCursor"]}
    ).json()
    assert len(first["items"]) == len(second["items"]) == 2
    assert not {o["objectId"] for o in first["items"]} & {
        o["objectId"] for o in second["items"]
    }
    assert populated.get(ROOT + "/dimension-members/src_product").status_code == 404
    assert (
        populated.get(
            ROOT.replace(MODEL, "another_model") + "/sources/src_product"
        ).status_code
        == 404
    )
    assert populated.get(ROOT + "/sources?limit=101").status_code == 422


def test_duplicate_identity_does_not_increment_revision(populated):
    before = populated.get(ROOT).json()["version"]
    assert (
        populated.post(ROOT + "/sources", json=definition("src_product")).status_code
        == 409
    )
    assert populated.get(ROOT).json()["version"] == before


@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        ("self", "dependency_cycle"),
        ("unknown", "invalid_reference"),
        ("arity", "function_arity"),
    ],
)
def test_formula_validation(populated, mutation, expected_code):
    payload = definition("measure_error")
    if mutation == "self":
        payload["definition"]["formula"]["expression"] = {
            "type": "measureRef",
            "measureId": "measure_error",
        }
    elif mutation == "unavailable":
        payload["definition"]["nativeDimensionId"] = "pl_plt"
    elif mutation == "unknown":
        payload["definition"]["formula"]["expression"] = {
            "type": "measureRef",
            "measureId": "missing",
        }
    else:
        expr = payload["definition"]["formula"]["expression"]
        expr["arguments"].append({"type": "literal", "value": 1})
    response = populated.put(
        ROOT + "/measures/measure_error", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422, response.text
    assert expected_code in {e["code"] for e in response.json()["error"]["details"]}


def test_invalid_transaction_key(populated):
    payload = definition("tx_sales")
    payload["definition"]["keyMappings"].pop()
    response = populated.put(
        ROOT + "/transactions/tx_sales", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422


def test_time_dimension_flag_contract(populated):
    payload = definition("dim_time")
    payload["isTimeDim"] = False
    assert (
        populated.put(
            ROOT + "/dimension-members/dim_time", json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 422
    )
    payload = definition("dim_time")
    payload["objectId"] = "dim_other_time"
    assert populated.post(ROOT + "/dimension-members", json=payload).status_code == 422


def test_empty_model_and_revision_crud(client):
    payload = {"modelId": "empty", "name": "Empty", "displayName": "Empty"}
    assert client.post("/api/v1/models", json=payload).status_code == 201
    assert (
        client.put(
            "/api/v1/models/empty",
            json={"name": "Renamed", "displayName": "Renamed"},
            headers={"If-Match": '"1"'},
        ).status_code
        == 200
    )
    root = "/api/v1/models/empty/revisions"
    assert (
        client.post(
            root, json={"revisionId": "draft", "expectedModelVersion": 2}
        ).status_code
        == 201
    )
    assert client.post(root + "/draft/validate").json()["valid"] is False
    assert (
        client.delete("/api/v1/models/empty", headers={"If-Match": '"3"'}).status_code
        == 409
    )
    assert (
        client.delete(root + "/draft", headers={"If-Match": '"1"'}).status_code == 204
    )
    assert (
        client.delete("/api/v1/models/empty", headers={"If-Match": '"4"'}).status_code
        == 204
    )
    assert client.get("/api/v1/models/empty").status_code == 404


def test_unreferenced_object_crud(populated):
    payload = definition("src_product")
    payload["objectId"] = "src_unused"
    assert populated.post(ROOT + "/sources", json=payload).status_code == 201
    payload["description"] = "Updated catalog description"
    assert (
        populated.put(
            ROOT + "/sources/src_unused", json=payload, headers={"If-Match": '"1"'}
        ).status_code
        == 200
    )
    assert (
        populated.delete(
            ROOT + "/sources/src_unused", headers={"If-Match": '"2"'}
        ).status_code
        == 204
    )


def test_base_cannot_be_a_coarser_projection(populated):
    response = populated.put(
        ROOT,
        json={"baseDimensionId": "pl_pt", "changeReason": "Invalid coarser base"},
        headers={"If-Match": etag(populated, ROOT)},
    )
    assert response.status_code == 422
    assert "base_not_finest" in {d["code"] for d in response.json()["error"]["details"]}


def test_if_match_rejects_oversized_integer(populated):
    response = populated.put(
        ROOT + "/dimension-members/dim_product",
        json=definition("dim_product"),
        headers={"If-Match": "1" * 5000},
    )
    assert response.status_code == 400


def test_openapi_exposes_typed_contexts(client):
    schema = client.get("/openapi.json").json()
    assert "/api/v1/warehouse/imports/csv" in schema["paths"]
    assert "SourceInput" in schema["components"]["schemas"]
    assert any(
        name.startswith("CalculatedMeasureDefinition")
        for name in schema["components"]["schemas"]
    )
    assert (
        "displayName"
        not in schema["components"]["schemas"]["SourceColumn"]["properties"]
    )


def test_dimension_lookup_can_use_non_primary_attribute(populated):
    payload = definition("pl_plt")
    dimension = definition("dim_product")
    alternate = next(
        c["columnId"]
        for c in dimension["definition"]["columns"]
        if c["columnId"] not in dimension["definition"]["keyColumnIds"]
    )
    binding = next(
        b
        for b in payload["definition"]["memberBindings"]
        if b["memberCatalogId"] == "dim_product"
    )
    binding["keyMappings"][0]["memberColumnId"] = alternate
    response = populated.put(
        ROOT + "/dimensions/pl_plt", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 200, response.text
    saved = next(
        b
        for b in response.json()["definition"]["memberBindings"]
        if b["memberCatalogId"] == "dim_product"
    )
    assert saved["keyMappings"][0]["memberColumnId"] == alternate
    assert saved["lookupCardinality"] == "many_to_one"
    assert populated.post(ROOT + "/validate").json()["valid"] is True


@pytest.mark.parametrize("target", ["unknown_attribute", "date"])
def test_dimension_lookup_rejects_column_outside_the_bound_dimension(populated, target):
    payload = definition("pl_plt")
    binding = next(
        b
        for b in payload["definition"]["memberBindings"]
        if b["memberCatalogId"] == "dim_product"
    )
    binding["keyMappings"][0]["memberColumnId"] = target
    response = populated.put(
        ROOT + "/dimensions/pl_plt", json=payload, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422, response.text


def test_new_transaction_defaults_to_many_and_preserves_explicit_legacy_declarations(
    populated,
):
    payload = definition("tx_sales")
    payload["objectId"] = "tx_sales_many"
    payload["name"] = "SalesMany"
    payload["definition"].pop("expectedRowsPerGrain", None)
    response = populated.post(ROOT + "/transactions", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["definition"]["expectedRowsPerGrain"] == "many"
    assert (
        populated.get(ROOT + "/transactions/tx_sales").json()["definition"][
            "expectedRowsPerGrain"
        ]
        == "zero_or_one"
    )


def test_none_disaggregation_repeats_and_allows_finer_formula(populated):
    payload = definition("measure_error")
    payload["definition"]["nativeDimensionId"] = "pl_plt"
    response = populated.put(
        ROOT + "/measures/measure_error", json=payload, headers={"If-Match": "1"}
    )
    assert response.status_code == 200, response.text


def test_base_allows_an_equivalent_projected_level(populated):
    base = definition("pl_plt")
    keys = base["definition"]["grain"]["keyIds"]
    payload = {
        "objectId": "equivalent_base",
        "kind": "dimension",
        "name": "Equivalent",
        "displayName": "Equivalent",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourceDimensionId": "pl_plt",
            },
            "grain": {
                "keyIds": keys,
                "columns": [
                    {
                        "grainColumnId": c["grainColumnId"],
                        "name": c["name"],
                        "populationRef": {"sourceGrainColumnId": c["grainColumnId"]},
                    }
                    for c in base["definition"]["grain"]["columns"]
                    if c["grainColumnId"] in keys
                ],
            },
        },
    }
    response = populated.post(ROOT + "/dimensions", json=payload)
    assert response.status_code == 201, response.text
