from copy import deepcopy

from .conftest import ROOT, object_payload


def command(client, *, new_population=True, transaction_id="tx_population"):
    transaction = object_payload(client.get(ROOT + "/transactions/tx_forecast").json())
    transaction.update(
        objectId=transaction_id,
        name="Population forecast",
        displayName="Population forecast",
    )
    population = object_payload(client.get(ROOT + "/dimensions/pl_plt").json())
    if new_population:
        population.update(
            objectId="population_plt", name="Active PLT", displayName="Active PLT"
        )
        transaction["definition"]["dimensionId"] = population["objectId"]
    return {
        "transaction": transaction,
        "population": population if new_population else None,
        "expectedVersion": None,
        "expectedPopulationVersion": None,
        "expectedRevisionVersion": client.get(ROOT).json()["version"],
        "useAsBase": new_population,
    }


def test_creates_population_transaction_measures_and_base_atomically(populated):
    payload = command(populated)
    measure = populated.get(ROOT + "/measures/measure_fqty").json()["definition"]
    payload["transaction"]["measures"] = [
        {
            "valueColumnId": measure["valueColumnId"],
            "editable": False,
            "aggregation": measure["aggregation"],
            "allocation": {"mode": "weighted_by_self"},
        }
    ]
    before = populated.get(ROOT).json()["version"]
    response = populated.post(ROOT + "/transactions/population/save", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["effectiveGrainId"] == "population_plt"
    revision = populated.get(ROOT).json()
    assert revision["baseDimensionId"] == "population_plt"
    assert revision["version"] == before + 1
    assert populated.get(ROOT + "/dimensions/population_plt").status_code == 200
    measures = populated.get(ROOT + "/measures").json()["items"]
    assert (
        len(
            [
                m
                for m in measures
                if m["definition"].get("transactionId") == "tx_population"
            ]
        )
        == 1
    )


def test_reuses_population_without_creating_another(populated):
    before = populated.get(ROOT + "/dimensions").json()
    payload = command(populated, new_population=False)
    response = populated.post(ROOT + "/transactions/population/save", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["definition"]["dimensionId"] == "pl_plt"
    assert populated.get(ROOT + "/dimensions").json() == before


def test_invalid_mapping_rolls_back_population_base_and_transaction(populated):
    payload = command(populated)
    payload["transaction"]["definition"]["keyMappings"][0]["sourceColumnId"] = "missing"
    before = populated.get(ROOT).json()
    measures = populated.get(ROOT + "/measures").json()
    response = populated.post(ROOT + "/transactions/population/save", json=payload)
    assert response.status_code == 422, response.text
    assert populated.get(ROOT).json() == before
    assert populated.get(ROOT + "/dimensions/population_plt").status_code == 404
    assert populated.get(ROOT + "/transactions/tx_population").status_code == 404
    assert populated.get(ROOT + "/measures").json() == measures


def test_stale_revision_and_population_do_not_overwrite(populated):
    payload = command(populated)
    payload["expectedRevisionVersion"] -= 1
    response = populated.post(ROOT + "/transactions/population/save", json=payload)
    assert response.status_code == 409, response.text
    payload["expectedRevisionVersion"] += 1
    payload["population"] = object_payload(
        populated.get(ROOT + "/dimensions/pl_plt").json()
    )
    payload["transaction"]["definition"]["dimensionId"] = "pl_plt"
    # An existing definition requires its exact version, even on the create path.
    response = populated.post(ROOT + "/transactions/population/save", json=payload)
    assert response.status_code == 409, response.text


def test_rebinding_existing_transaction_uses_existing_measure_rules(populated):
    old = populated.get(ROOT + "/transactions/tx_forecast").json()
    payload = command(populated)
    payload["transaction"] = object_payload(old)
    payload["transaction"]["definition"]["dimensionId"] = "population_plt"
    payload["expectedVersion"] = old["version"]
    response = populated.post(ROOT + "/transactions/population/save", json=payload)
    assert response.status_code == 200, response.text
    assert populated.get(ROOT + "/measures/measure_fqty").json()["version"] == 2


def test_updates_shared_population_with_optimistic_concurrency(populated):
    payload = command(populated, new_population=False)
    population = populated.get(ROOT + "/dimensions/pl_plt").json()
    payload["population"] = object_payload(population)
    payload["population"]["description"] = "Shared valid combinations"
    payload["expectedPopulationVersion"] = population["version"]
    response = populated.post(ROOT + "/transactions/population/save", json=payload)
    assert response.status_code == 200, response.text
    current = populated.get(ROOT + "/dimensions/pl_plt").json()
    assert current["description"] == "Shared valid combinations"
    assert current["version"] == population["version"] + 1
    assert (
        populated.get(ROOT + "/transactions/tx_forecast").json()["definition"][
            "dimensionId"
        ]
        == "pl_plt"
    )


def test_population_must_match_transaction_and_have_separate_identity(populated):
    payload = command(populated)
    bad = deepcopy(payload)
    bad["transaction"]["definition"]["dimensionId"] = "pl_plt"
    assert (
        populated.post(ROOT + "/transactions/population/save", json=bad).status_code
        == 422
    )
    bad = deepcopy(payload)
    bad["transaction"]["objectId"] = bad["population"]["objectId"]
    assert (
        populated.post(ROOT + "/transactions/population/save", json=bad).status_code
        == 422
    )


def test_unsaved_population_exposes_multi_grain_targets_and_schema(populated):
    payload = command(populated)
    options = populated.post(
        ROOT + "/transactions/population/options", json=payload["population"]
    )
    assert options.status_code == 200, options.text
    assert options.json()["targets"]
    schema = populated.post(ROOT + "/transactions/population/schema", json=payload)
    assert schema.status_code == 200, schema.text
    assert schema.json()["columns"]
    assert populated.get(ROOT + "/dimensions/population_plt").status_code == 404


def test_explicit_time_key_survives_population_connection_and_projection():
    from backend.app.engine.relationships import LevelRelationshipAnalyzer

    population = {
        "objectId": "plt",
        "kind": "dimension",
        "name": "PLT",
        "definition": {
            "population": {"operation": "source", "sourceId": "source"},
            "grain": {
                "keyIds": ["product", "week"],
                "columns": [
                    {
                        "grainColumnId": "product",
                        "name": "product",
                        "populationRef": {"columnId": "col_1"},
                    },
                    {
                        "grainColumnId": "week",
                        "name": "week",
                        "populationRef": {"columnId": "col_2"},
                    },
                ],
            },
            "memberBindings": [],
        },
    }
    transaction = {
        "objectId": "forecast",
        "kind": "transaction",
        "name": "Forecast",
        "definition": {
            "dimensionId": "plt",
            "sourceId": "source",
            "timeColumnId": "source_week",
            "keyMappings": [{"grainColumnId": "week", "sourceColumnId": "source_week"}],
        },
    }
    projection = {
        "objectId": "weeks",
        "kind": "dimension",
        "name": "Weeks",
        "definition": {
            "population": {
                "operation": "distinct_projection",
                "sourceDimensionId": "plt",
            },
            "grain": {
                "keyIds": ["week"],
                "columns": [
                    {
                        "grainColumnId": "week",
                        "name": "Week",
                        "populationRef": {"sourceGrainColumnId": "week"},
                    }
                ],
            },
            "memberBindings": [],
        },
    }
    analyzer = LevelRelationshipAnalyzer([population, transaction, projection])
    assert analyzer.outputs("plt")["week"].is_time
    assert not analyzer.outputs("plt")["product"].is_time
    assert analyzer.outputs("weeks")["week"].is_time
