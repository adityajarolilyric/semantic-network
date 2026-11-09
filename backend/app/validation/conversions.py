"""A conversion factor must be uniquely determined at the value's native grain."""

from ..domain.typed_values import NUMERIC
from ..formula_language.expressions import conversion_factor_references
from ..engine.graph.relationships import LevelRelationshipAnalyzer


def validate_conversion(context, owner):
    definition = owner["definition"]
    if (
        definition.get("isConversionFactor")
        and definition["resultType"]["type"] not in NUMERIC
    ):
        context.error(
            owner,
            "conversion_type",
            "Conversion factors require a numeric source column.",
        )
    if definition.get("isConversionFactor"):
        transaction = context.objects.get(definition["transactionId"], {}).get(
            "definition", {}
        )
        values = (transaction.get("resolution") or {}).get("values", [])
        if any(
            value["sourceColumnId"] == definition["valueColumnId"]
            and value["mode"] == "split"
            for value in values
        ):
            context.error(
                owner,
                "conversion_factor_split",
                "Conversion factors cannot be split during multi-grain resolution. Use Copy for this value column.",
            )
    conversion = definition.get("conversion")
    if not conversion:
        return
    factor = context.reference(
        owner,
        conversion["factorMeasureId"],
        "measure",
        "definition.conversion.factorMeasureId",
    )
    if definition["resultType"]["type"] not in NUMERIC:
        context.error(
            owner,
            "conversion_type",
            "Unit/currency conversion requires a numeric value.",
        )
    if not factor:
        return
    factor_definition = factor["definition"]
    if not factor_definition.get("isConversionFactor"):
        context.error(
            owner,
            "conversion_factor",
            "Choose a referenced measure marked as a conversion factor.",
        )
    # Finer rates cannot be averaged into a coarser amount. Convert at the finer
    # grain first, then aggregate converted amounts.
    relation = LevelRelationshipAnalyzer(list(context.objects.values())).analyze(
        definition["nativeGrainId"], factor_definition["nativeGrainId"]
    )["relationship"]
    if relation not in {"same", "equivalent", "roll_up"}:
        context.error(
            owner,
            "conversion_grain",
            "The factor must be determined by every native row. Convert at a finer grain before aggregating if rates vary within a native row.",
        )
    availability = context.measures_rules.availability(
        factor, definition["nativeGrainId"]
    )
    if not availability.available:
        context.error(owner, "conversion_grain", availability.reason)


def validate_conversion_formula(context, owner):
    definition = owner["definition"]
    factors = conversion_factor_references(
        definition["formula"]["expression"], context.objects
    )
    if not factors:
        return
    if definition.get("calculationType") != "CalculateFirst":
        context.error(
            owner,
            "conversion_calculation",
            "Formulas using conversion factors require CalculateFirst: convert native values before aggregating them.",
        )
    relationships = LevelRelationshipAnalyzer(list(context.objects.values()))
    for measure_id in factors:
        factor = context.objects[measure_id]
        if relationships.analyze(
            definition["nativeGrainId"], factor["definition"]["nativeGrainId"]
        )["relationship"] not in {"same", "equivalent", "roll_up"}:
            context.error(
                owner,
                "conversion_grain",
                f'The output grain must determine "{factor["name"]}". Convert at the factor grain or finer before aggregating.',
            )
