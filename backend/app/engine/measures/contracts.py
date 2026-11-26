"""Stable internal value-column names shared by scalar and relational operators."""

from dataclasses import dataclass

VALUE = "__value"
COMPLETE = "__complete"


LEGACY_MEASURE_COMPILER_VERSION = (
    "measure-v4-aggregation-derived-round6-sqlglot-27.28.1"
)

TYPED_MEASURE_COMPILER_VERSION = "measure-v5-typed-formulas-native-only"

MEASURE_COMPILER_VERSION = "measure-v6-planning-language-counts"

MATERIALISATION_POLICY = "aggregation-derived-disaggregation-v1-round6"


@dataclass(frozen=True)
class MeasureNode:
    measure_id: str
    level_id: str
    operation: str
