from copy import deepcopy

import pytest

from .conftest import ROOT, object_payload
from .test_transaction_grains import command


def selection(column, **changes):
    return {
        "valueColumnId": column,
        "editable": True,
        "aggregation": {"nonTime": "avg", "time": "max"},
        **changes,
    }


def numeric_column(client):
    return client.get(ROOT + "/sources/src_forecast").json()["definition"]["columns"][
        3
    ]["columnId"]


def mapped_command(client):
    original = client.get(ROOT + "/transactions").json()["items"]
    original = next(
        t for t in original if t["definition"]["sourceId"] == "src_forecast"
    )
    payload = object_payload(original)
    payload["objectId"] = "auto_measures"
    payload["name"] = "Auto measures"
    return payload


@pytest.mark.parametrize("generated", [False, True])
def test_selected_measure_defaults_and_atomic_creation(populated, generated):
    payload = (
        command(populated, "auto_measures") if generated else mapped_command(populated)
    )
    column = numeric_column(populated)
    payload["measures"] = [selection(column)]
    url = ROOT + ("/transactions/from-grain" if generated else "/transactions")
    response = populated.post(url, json=payload)
    assert response.status_code == 201, response.text
    transaction = response.json()["transaction"] if generated else response.json()
    assert "measures" not in transaction
    measures = populated.get(ROOT + "/measures").json()["items"]
    created = [
        m for m in measures if m["definition"].get("transactionId") == "auto_measures"
    ]
    assert len(created) == 1
    measure = created[0]
    assert measure["name"] == "forecast_qty"
    definition = measure["definition"]
    assert "nativeDimensionId" not in definition
    assert "nativeGrainId" not in definition
    assert definition["transactionId"] == transaction["objectId"]
    assert definition["editable"] is True
    assert definition["resultType"] == {
        "type": "number",
        "precision": None,
        "scale": None,
        "nullable": True,
    }
    assert definition["aggregation"] == {
        "nonTime": "avg",
        "time": "max",
        "order": "nonTimeThenTime",
        "nullInputPolicy": "ignore",
        "allNullResult": "null",
    }
    assert definition["allocation"]["mode"] == "weighted_by_self"
    assert definition["missingTransactionValue"] == "null"
    assert definition["sourceNullPolicy"] == "preserve"
    if generated:
        revision = populated.get(ROOT).json()
        assert populated.post(url, json=payload).json() == response.json()
        assert populated.get(ROOT).json() == revision


@pytest.mark.parametrize("generated", [False, True])
@pytest.mark.parametrize("invalid", ["string", "missing", "duplicate", "basis"])
def test_invalid_selection_leaves_no_partial_metadata(populated, generated, invalid):
    payload = (
        command(populated, "auto_measures") if generated else mapped_command(populated)
    )
    column = numeric_column(populated)
    payload["measures"] = [selection(column)]
    if invalid == "string":
        payload["measures"][0]["valueColumnId"] = populated.get(
            ROOT + "/sources/src_forecast"
        ).json()["definition"]["columns"][0]["columnId"]
    elif invalid == "missing":
        payload["measures"][0]["valueColumnId"] = "missing"
    elif invalid == "duplicate":
        payload["measures"].append(deepcopy(payload["measures"][0]))
    else:
        payload["measures"][0]["allocation"] = {
            "mode": "weighted",
            "basisMeasureId": "missing",
        }
    before = populated.get(ROOT).json()
    measures = populated.get(ROOT + "/measures").json()
    levels = populated.get(ROOT + "/dimensions").json()
    response = populated.post(
        ROOT + ("/transactions/from-grain" if generated else "/transactions"),
        json=payload,
    )
    assert response.status_code == 422, response.text
    assert populated.get(ROOT).json() == before
    assert populated.get(ROOT + "/transactions/auto_measures").status_code == 404
    assert populated.get(ROOT + "/measures").json() == measures
    assert populated.get(ROOT + "/dimensions").json() == levels


@pytest.mark.parametrize("generated", [False, True])
def test_object_limit_counts_selected_measures(populated, generated):
    payload = (
        command(populated, "auto_measures") if generated else mapped_command(populated)
    )
    payload["measures"] = [selection(numeric_column(populated))]
    count = sum(
        len(populated.get(ROOT + "/" + resource).json()["items"])
        for resource in [
            "sources",
            "dimensions",
            "transactions",
            "measures",
        ]
    )
    service = populated.app.state.container.transactions
    service.max_objects = count + 1
    before = populated.get(ROOT).json()
    response = populated.post(
        ROOT + ("/transactions/from-grain" if generated else "/transactions"),
        json=payload,
    )
    assert response.status_code == 409, response.text
    assert populated.get(ROOT).json() == before
    assert populated.get(ROOT + "/transactions/auto_measures").status_code == 404
