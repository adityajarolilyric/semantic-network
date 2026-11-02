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


MEASURE_TYPE_POLICY = "float64-referenced-v2"


def normalize_measure(document):
    """Interpret legacy types without mutating immutable stored revision records."""
    if document.get("kind") != "measure":
        return document
    definition = document["definition"]
    result = definition["resultType"]
    if definition["measureType"] == "referenced" or result["type"] == "integer":
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
