import re

from ..core.errors import MetadataError


def referenced_result_type(physical_type: str, nullable: bool) -> dict | None:
    """Validate numeric sources; referenced execution always uses Float64."""
    value = re.sub(r"\s+", "", physical_type).lower()
    while value.startswith(("nullable(", "lowcardinality(")) and value.endswith(")"):
        value = value[value.index("(") + 1 : -1]
    if re.fullmatch(r"u?int(8|16|32|64|128|256)", value):
        return {"type": "number", "nullable": nullable}
    if re.fullmatch(r"float(32|64)", value):
        return {"type": "number", "nullable": nullable}
    decimal = re.fullmatch(r"decimal\((\d+),(\d+)\)", value)
    sized_decimal = re.fullmatch(r"decimal(32|64|128|256)\((\d+)\)", value)
    if decimal:
        precision, scale = map(int, decimal.groups())
    elif sized_decimal:
        precision = {"32": 9, "64": 18, "128": 38, "256": 76}[sized_decimal[1]]
        scale = int(sized_decimal[2])
    else:
        return None
    if not (1 <= precision <= 76 and 0 <= scale <= precision):
        raise MetadataError(
            "invalid_source_type", "The source column has an invalid decimal type."
        )
    return {"type": "number", "nullable": nullable}


MEASURE_TYPE_POLICY = "float64-referenced-v3-preserve-source-nulls"
TYPED_MEASURE_TYPE_POLICY = "typed-measures-v1-native-only-nonnumeric"


def normalize_measure(document):
    """Interpret legacy types without mutating immutable stored revision records."""
    if document.get("kind") != "measure":
        return document
    definition = document["definition"]
    from ..formula_language.compatibility import normalize_count_definition

    definition = normalize_count_definition(definition)
    document = {**document, "definition": definition}
    # Omitted policies in saved metadata meant Repeat before the authoring default changed.
    if (
        definition["measureType"] == "referenced"
        or definition.get("calculationType") == "CalculateFirst"
    ):
        allocation = definition.get("allocation")
        if definition["resultType"]["type"] in {"string", "date", "boolean"}:
            allocation = None
        elif allocation is None:
            allocation = (
                {"mode": "none"} if definition["measureType"] == "referenced" else None
            )
        elif allocation.get("mode") is None:
            allocation = {**allocation, "mode": "none"}
        document = {**document, "definition": {**definition, "allocation": allocation}}
        definition = document["definition"]
    if definition["measureType"] == "referenced":
        document = {
            **document,
            "definition": {**definition, "sourceNullPolicy": "preserve"},
        }
        definition = document["definition"]
    result = definition["resultType"]
    if result["type"] == "integer" or (
        definition["measureType"] == "referenced"
        and result["type"] in {"number", "decimal"}
    ):
        return {
            **document,
            "definition": {
                **definition,
                "resultType": {
                    "type": "number",
                    "nullable": result.get("nullable", True),
                },
            },
        }
    return document
