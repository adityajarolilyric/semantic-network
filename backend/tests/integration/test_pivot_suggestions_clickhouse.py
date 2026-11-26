# ruff: noqa: F811
import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed

pytestmark = pytest.mark.integration


def suggest(client, source):
    response = client.post(
        BASE + "/managed-tables/pivot-suggestion", json={"tableId": source}
    )
    assert response.status_code == 200, response.text
    return response.json()


def preview(client, source, pivot):
    response = client.post(
        BASE + "/transactions/input/preview",
        json={"objectId": "suggested_transaction", "sourceId": source, "pivot": pivot},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_suggestion_reads_managed_input_and_preserves_multi_pair_records(
    clickhouse_app,
):
    client, _, _, _ = clickhouse_app
    source = managed(
        client,
        "ForecastSuggest",
        b"product_attribute,product_value,location_attribute,location_value,week,forecast_qty\nBrand,B1,Zone,Z1,W1,100\nBrand,B2,Zone,Z2,W1,100\n",
    )
    before = {
        kind: client.get(BASE + "/" + kind).json()
        for kind in ("sources", "dimensions", "transactions", "measures")
    }
    suggestion = suggest(client, source)
    assert len(suggestion["pivot"]["pairs"]) == 2
    assert suggestion["pivot"]["recordKeyColumnIds"] == []
    assert suggestion["rowsExamined"] == 2 and suggestion["complete"]
    assert suggest(client, source) == suggestion
    result = preview(client, source, suggestion["pivot"])
    assert result["rowCount"] == 2
    names = {column["name"] for column in result["columns"]}
    assert names == {"Brand", "Zone", "week", "forecast_qty"}
    for kind, authored in before.items():
        assert client.get(BASE + "/" + kind).json() == authored
    assert (
        client.post(
            BASE + "/managed-tables/pivot-suggestion", json={"tableId": "missing"}
        ).status_code
        == 404
    )


def test_separate_rows_are_grouped_by_inferred_record_identifier(clickhouse_app):
    client, _, _, _ = clickhouse_app
    source = managed(
        client,
        "CustomerSuggest",
        b"customer_id,attribute_id,attribute_value\nC1,Channel,Retail\nC1,Sold To,S1\nC2,Channel,Wholesale\nC2,Sold To,S2\n",
    )
    suggestion = suggest(client, source)
    assert suggestion["pivot"]["recordKeyColumnIds"] == ["col_1"]
    assert suggestion["confidence"] == "high"
    result = preview(client, source, suggestion["pivot"])
    assert result["rowCount"] == 2
    assert {column["name"] for column in result["columns"]} == {
        "customer_id",
        "Channel",
        "Sold To",
    }
    wide = managed(
        client, "WideSuggest", b"product_id,promo_type,qty\nP1,Discount,100\n"
    )
    assert suggest(client, wide)["pivot"] is None


def test_suggestion_profiles_at_most_one_hundred_rows(clickhouse_app):
    client, _, _, _ = clickhouse_app
    content = "attribute,value\n" + "".join(f"Brand,B{i}\n" for i in range(150))
    source = managed(client, "SampleSuggest", content.encode())
    result = suggest(client, source)
    assert result["rowsExamined"] == 100
    assert result["complete"] is False
    assert result["pivot"] is not None
    assert any("100" in warning for warning in result["warnings"])
