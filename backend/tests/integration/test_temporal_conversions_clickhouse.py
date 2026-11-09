"""Real SQL, materialisation and edit regressions for calendar windows/conversions."""
# ruff: noqa: F811

import pytest

from .test_csv_clickhouse import clickhouse_app  # noqa: F401
from .test_planning_clickhouse import BASE, managed, create, build
from .test_measures_clickhouse import (
    AGG,
    transaction,
    assert_post,
    build_values,
    values,
)
from .test_managed_clickhouse import await_run
from .test_measure_edits_clickhouse import edit
from .test_edit_cell_scopes_clickhouse import changed_rows

pytestmark = pytest.mark.integration
CONTEXT = {
    "timeKeyId": "period",
    "orderColumnId": "period",
    "periodUnit": "index",
    "partitionColumnIds": ["product"],
}


def seed(client, data=None, attributes=None):
    client.app.state.container.warehouse_sources.upload_metadata = None
    source = managed(
        client,
        "Demand",
        data
        or b"product,period,qty,factor\nP1,1,10,2\nP1,2,20,2\nP1,4,40,2\nP1,5,50,2\nP2,1,100,3\nP2,2,200,3\n",
    )
    create(
        client,
        {
            "objectId": "base",
            "kind": "dimension",
            "name": "ProductPeriod",
            "displayName": "ProductPeriod",
            "definition": {
                "population": {"operation": "source", "sourceId": source},
                "populationAttributes": attributes or [],
                "grain": {
                    "keyIds": ["product", "period"],
                    "columns": [
                        {
                            "grainColumnId": key,
                            "name": key,
                            "populationRef": {"columnId": col},
                        }
                        for key, col in [("product", "col_1"), ("period", "col_2")]
                    ],
                },
            },
        },
    )
    revision = client.get(BASE).json()
    response = client.put(
        BASE,
        json={"baseDimensionId": "base", "changeReason": ""},
        headers={"If-Match": str(revision["version"])},
    )
    assert response.status_code == 200, response.text
    transaction(
        client, "demand", source, "base", [("product", "col_1"), ("period", "col_2")]
    )
    referenced(client, "Qty", "col_3", editable=True)
    assert build(client, "base")["state"] == "succeeded"
    return source


def referenced(client, name, column, **extra):
    return assert_post(
        client,
        "/measures",
        {
            "objectId": name,
            "kind": "measure",
            "name": name,
            "displayName": name,
            "business": {},
            "definition": {
                "measureType": "referenced",
                "transactionId": "demand",
                "valueColumnId": column,
                "aggregation": AGG,
                "allocation": {"mode": "none"},
                **extra,
            },
        },
    )


def calculated(client, name, text, context=CONTEXT, calculation="CalculateFirst"):
    return client.post(
        BASE + "/measures",
        json={
            "objectId": name,
            "kind": "measure",
            "name": name,
            "displayName": name,
            "business": {},
            "definition": {
                "measureType": "calculated",
                "outputGrain": {"kind": "dimension", "objectId": "base"},
                "calculationType": calculation,
                "aggregation": AGG if calculation == "CalculateFirst" else None,
                "allocation": {"mode": "none"}
                if calculation == "CalculateFirst"
                else None,
                "temporal": context,
                "formula": {"displayText": text, "languageVersion": "planning-v1"},
            },
        },
    )


def read(client):
    result, columns = values(client, "base")
    return {
        (row[columns["product"]], int(row[columns["period"]])): {
            key: None if row[index] is None else float(row[index])
            for key, index in columns.items()
            if key not in {"product", "period"}
        }
        for row in result["rows"]
    }


def test_windows_skip_missing_periods_and_nested_dependencies_refresh_only_affected_cells(
    clickhouse_app,
):
    client, *_ = clickhouse_app
    seed(client)
    for name, formula in [
        ("Rolling", 'ROLLING([Qty],"SUM",-1,0)'),
        ("Running", 'CUMULATIVE([Qty],"SUM")'),
        ("Previous", "LAG([Qty],1)"),
        ("Next", "LEAD([Qty],1)"),
        ("Nested", 'ROLLING(ROLLING([Qty],"SUM",-1,0),"SUM",-1,0)'),
        ("Root", "ROOT(POWER([Qty], 3), 3)"),
    ]:
        response = calculated(
            client, name, formula, CONTEXT if name != "Root" else None
        )
        assert response.status_code == 201, response.text
    run = build_values(client)
    assert run["state"] == "succeeded", run
    before = read(client)
    assert [before["P1", p]["Rolling"] for p in [1, 2, 4, 5]] == [10, 30, 40, 90]
    assert [before["P1", p]["Running"] for p in [1, 2, 4, 5]] == [10, 30, 70, 120]
    assert [before["P1", p]["Previous"] for p in [1, 2, 4, 5]] == [None, 10, None, 40]
    assert [before["P1", p]["Next"] for p in [1, 2, 4, 5]] == [20, None, 50, None]
    assert before["P1", 2]["Nested"] == 40
    assert before["P1", 5]["Root"] == 50
    response = edit(
        client,
        mid="Qty",
        value=15,
        members=["P1", "1"],
        rows=[
            {"dimensionId": "base", "columnId": key} for key in ["product", "period"]
        ],
    )
    run = await_run(client, BASE, response)
    assert run["state"] == "succeeded", run
    after = read(client)
    assert [after["P1", p]["Running"] for p in [1, 2, 4, 5]] == [15, 35, 75, 125]
    assert after["P1", 2]["Previous"] == 15
    assert after["P1", 2]["Nested"] == 50
    assert after["P1", 4]["Rolling"] == before["P1", 4]["Rolling"]
    assert after["P2", 1] == before["P2", 1]
    counts = changed_rows(client, run)["base"]
    assert counts["Running"] == 4 and counts["Rolling"] == 2 and counts["Previous"] == 1
    assert counts["Next"] == 0 and counts["Qty"] == 1


def test_conversion_factors_materialise_and_reference_conversion_matches_formula(
    clickhouse_app,
):
    client, *_ = clickhouse_app
    seed(client)
    factor = referenced(
        client, "Factor", "col_4", isConversionFactor=True, editable=True
    )
    assert not factor["definition"]["editable"]
    assert factor["definition"]["aggregation"]["nonTime"] == "single_value"
    referenced(
        client,
        "Converted",
        "col_3",
        conversion={
            "factorMeasureId": "Factor",
            "operation": "multiply",
            "targetUnit": "EA",
        },
    )
    referenced(
        client,
        "Divided",
        "col_3",
        conversion={
            "factorMeasureId": "Factor",
            "operation": "divide",
            "targetUnit": "Case",
        },
    )
    invalid = calculated(
        client,
        "InvalidConverted",
        "[Qty] * [Factor]",
        None,
        calculation="AggregateFirst",
    )
    assert invalid.status_code == 422, invalid.text
    assert "CalculateFirst" in invalid.text
    validation = client.post(
        BASE + "/measures/formula/validate",
        json={
            "objectId": "FormulaConverted",
            "name": "FormulaConverted",
            "nativeGrainId": "base",
            "displayText": "[Qty] * [Factor]",
            "calculationType": "CalculateFirst",
        },
    )
    assert validation.status_code == 200, validation.text
    assert (
        validation.json()["containsConversionFactors"] and validation.json()["valid"]
    ), validation.text
    response = calculated(client, "FormulaConverted", "[Qty] * [Factor]", None)
    assert response.status_code == 201, response.text
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result = read(client)
    assert result["P1", 1]["Converted"] == result["P1", 1]["FormulaConverted"] == 20
    assert result["P2", 2]["Converted"] == 600
    assert result["P1", 1]["Divided"] == 5
    assert client.get(BASE + "/measures/Converted").json()["business"]["unit"] == "EA"
    # Incremental no-change build must reuse conversions, not reapply the factor.
    run = build_values(client)
    assert run["state"] == "succeeded", run
    assert read(client) == result


@pytest.mark.parametrize(
    "source",
    [
        b"product,period,qty,factor\nP1,1,10,2\nP1,1,20,3\n",
        b"product,period,qty,factor\nP1,1,10,0\n",
        b"product,period,qty,factor\nP1,1,10,-2\n",
        b"product,period,qty,factor\nP1,1,10,2\nP2,1,20,\n",
    ],
)
def test_conflicting_or_nonpositive_factors_fail_without_publishing(
    clickhouse_app, source
):
    client, *_ = clickhouse_app
    seed(client, source)
    referenced(client, "Factor", "col_4", isConversionFactor=True)
    referenced(
        client,
        "Converted",
        "col_3",
        conversion={
            "factorMeasureId": "Factor",
            "operation": "multiply",
            "targetUnit": "EA",
        },
    )
    run = build_values(client)
    assert run["state"] == "failed", run
    assert "factor" in str(run).lower()


def test_temporal_validation_rejects_aggregate_first_and_invalid_bounds(clickhouse_app):
    client, *_ = clickhouse_app
    seed(client)
    response = calculated(
        client, "Bad", 'ROLLING([Qty],"SUM",-1,0)', calculation="AggregateFirst"
    )
    assert response.status_code == 422, response.text
    assert "CalculateFirst" in response.text
    for text in [
        'ROLLING([Qty],"SUM",2,0)',
        "LAG([Qty],0)",
        'ROLLING([Qty],"DELETE",-1,0)',
    ]:
        response = calculated(client, "Bad", text)
        assert response.status_code == 422, response.text


def test_all_window_operations_nulls_and_minimum_periods(clickhouse_app):
    client, *_ = clickhouse_app
    seed(
        client, b"product,period,qty,factor\nP1,1,10,2\nP1,2,,2\nP1,3,30,2\nP1,4,40,2\n"
    )
    for operation in ["AVG", "MIN", "MAX", "COUNT", "STDDEV_POP", "STDDEV_SAMP"]:
        response = calculated(client, operation, f'ROLLING([Qty],"{operation}",-2,0)')
        assert response.status_code == 201, response.text
    response = calculated(
        client, "Minimum", 'ROLLING([Qty],"SUM",-2,0)', {**CONTEXT, "minimumPeriods": 2}
    )
    assert response.status_code == 201, response.text
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result = read(client)
    assert result["P1", 3]["AVG"] == 20
    assert result["P1", 3]["COUNT"] == 2
    assert result["P1", 3]["MIN"] == 10
    assert result["P1", 3]["MAX"] == 30
    assert result["P1", 3]["STDDEV_POP"] == 10
    assert result["P1", 3]["STDDEV_SAMP"] == pytest.approx(14.142136)
    assert result["P1", 1]["STDDEV_SAMP"] is None
    assert result["P1", 1]["Minimum"] is None
    assert result["P1", 3]["Minimum"] == 40


def test_weekly_calendar_and_year_reset(clickhouse_app):
    client, *_ = clickhouse_app
    seed(
        client,
        b"product,period,qty,factor\nP1,2026-12-21,10,2026\nP1,2026-12-28,20,2026\nP1,2027-01-04,30,2027\nP1,2027-01-11,40,2027\n",
        attributes=[
            {"columnId": "year", "name": "year", "populationRef": {"columnId": "col_4"}}
        ],
    )
    context = {**CONTEXT, "periodUnit": "week", "resetColumnId": "year"}
    response = calculated(client, "YTD", 'CUMULATIVE([Qty],"SUM")', context)
    assert response.status_code == 201, response.text
    response = calculated(
        client,
        "TwoWeek",
        'ROLLING([Qty],"SUM",-1,0)',
        {**context, "resetColumnId": None},
    )
    assert response.status_code == 201, response.text
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result, columns = values(client, "base")
    assert [float(row[columns["YTD"]]) for row in result["rows"]] == [10, 30, 30, 70]
    assert [float(row[columns["TwoWeek"]]) for row in result["rows"]] == [
        10,
        30,
        50,
        70,
    ]


@pytest.mark.parametrize("with_base", [True, False])
def test_product_specific_factor_repeats_to_native_base_without_summing(
    clickhouse_app, with_base
):
    client, *_ = clickhouse_app
    seed(client)
    create(
        client,
        {
            "objectId": "products",
            "kind": "dimension",
            "name": "Products",
            "displayName": "Products",
            "definition": {
                "population": {
                    "operation": "distinct_projection",
                    "sourceDimensionId": "base",
                },
                "grain": {
                    "keyIds": ["product"],
                    "columns": [
                        {
                            "grainColumnId": "product",
                            "name": "product",
                            "populationRef": {"sourceGrainColumnId": "product"},
                        }
                    ],
                },
            },
        },
    )
    if not with_base:
        revision = client.get(BASE).json()
        response = client.put(
            BASE,
            json={"baseDimensionId": None, "changeReason": ""},
            headers={"If-Match": str(revision["version"])},
        )
        assert response.status_code == 200, response.text
    source = managed(client, "ProductFactors", b"product,factor\nP1,12\nP2,24\n")
    transaction(client, "rates", source, "products", [("product", "col_1")])
    assert_post(
        client,
        "/measures",
        {
            "objectId": "Factor",
            "kind": "measure",
            "name": "Factor",
            "displayName": "Factor",
            "business": {},
            "definition": {
                "measureType": "referenced",
                "transactionId": "rates",
                "valueColumnId": "col_2",
                "aggregation": AGG,
                "isConversionFactor": True,
            },
        },
    )
    referenced(
        client,
        "Converted",
        "col_3",
        conversion={
            "factorMeasureId": "Factor",
            "operation": "multiply",
            "targetUnit": "EA",
        },
    )
    assert build(client, "products")["state"] == "succeeded"
    run = build_values(client)
    assert run["state"] == "succeeded", run
    result = read(client)
    assert result["P1", 1]["Factor"] == result["P1", 5]["Factor"] == 12
    assert result["P1", 5]["Converted"] == 600
    assert result["P2", 2]["Converted"] == 4800


def test_arithmetic_domains_are_nullable_and_never_store_nan(clickhouse_app):
    from backend.app.formula_language.service import FormulaLanguage
    from backend.app.formula_language.sql import FormulaSqlCompiler

    _, admin, *_ = clickhouse_app
    cases = [
        ("SQRT(9)", 3),
        ("SQRT(-1)", None),
        ("ROOT(-8,3)", -2),
        ("ROOT(-16,2)", None),
        ("ROOT(16,0)", None),
        ("ROOT(16,2.5)", None),
        ("POWER(1e300,2)", None),
        ("CEIL(-1.2)", -1),
        ("FLOOR(-1.2)", -2),
    ]
    expressions = [
        FormulaSqlCompiler(None, None, None, {})
        .compile(FormulaLanguage.resolve(text, [], None))
        .sql(dialect="clickhouse")
        for text, _ in cases
    ]
    result = admin.query("SELECT " + ",".join(expressions)).result_rows[0]
    assert list(result) == [expected for _, expected in cases]
