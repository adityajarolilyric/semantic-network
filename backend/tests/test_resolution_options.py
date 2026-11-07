from backend.tests.conftest import ROOT, object_payload


def test_match_targets_are_only_actual_native_outputs(populated):
    response = populated.get(
        ROOT + "/transactions/resolution/options?dimensionId=pl_pt"
    )
    assert response.status_code == 200, response.text
    targets = response.json()["targets"]
    assert {option["target"]["columnId"] for option in targets} == {
        "pt_product",
        "pt_date",
    }
    assert all(option["target"]["memberCatalogId"] is None for option in targets)
    assert not any(
        "Product Name" in option["label"] or "Location" in option["label"]
        for option in targets
    )
    product = next(
        option for option in targets if option["target"]["columnId"] == "pt_product"
    )
    assert {"memberCatalogId": "dim_product", "columnId": "product_id"} in product[
        "aliases"
    ]
    assert (
        populated.get(ROOT + "/transactions/resolution/options").json()["targets"] == []
    )


def test_selected_dimension_attributes_are_native_choices_with_saved_reference_aliases(
    populated,
):
    original = populated.get(ROOT + "/dimensions/pl_pt").json()
    payload = object_payload(original)
    binding = next(
        binding
        for binding in payload["definition"]["memberBindings"]
        if binding["memberCatalogId"] == "dim_product"
    )
    binding["selectedAttributeColumnIds"] = ["product_name"]
    saved = populated.put(
        ROOT + "/dimensions/pl_pt",
        json=payload,
        headers={"If-Match": str(original["version"])},
    )
    assert saved.status_code == 200, saved.text
    targets = populated.get(
        ROOT + "/transactions/resolution/options?dimensionId=pl_pt"
    ).json()["targets"]
    assert len(targets) == 3
    name = next(option for option in targets if "Product Name" in option["label"])
    assert name["target"]["memberCatalogId"] is None
    assert {"memberCatalogId": "dim_product", "columnId": "product_name"} in name[
        "aliases"
    ]
