"""Canonical function registry shared by language discovery and semantic typing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Function:
    name: str
    minimum: int
    maximum: int
    category: str
    description: str


FUNCTION_CATALOG = tuple(
    Function(*row)
    for row in [
        ("abs", 1, 1, "scalar", "Absolute numeric value."),
        ("round", 1, 2, "scalar", "Round a number; optionally specify decimal places."),
        ("coalesce", 2, 16, "scalar", "First non-NULL value of matching types."),
        ("min", 2, 16, "scalar", "Smallest of the supplied scalar values."),
        ("max", 2, 16, "scalar", "Largest of the supplied scalar values."),
        (
            "if",
            3,
            3,
            "scalar",
            "Boolean condition, matching true and false result types.",
        ),
        ("and", 2, 16, "scalar", "True when all conditions are true."),
        ("or", 2, 16, "scalar", "True when any condition is true."),
        ("not", 1, 1, "scalar", "Invert a Boolean condition."),
        ("concat", 2, 16, "scalar", "Join String values."),
        ("upper", 1, 1, "scalar", "Uppercase text."),
        ("lower", 1, 1, "scalar", "Lowercase text."),
        ("trim", 1, 1, "scalar", "Remove outer whitespace."),
        ("date", 1, 1, "scalar", "Convert an ISO date string to Date."),
        ("add_days", 2, 2, "scalar", "Add a whole-number day count to a Date."),
        ("days_between", 2, 2, "scalar", "Days from the first Date to the second."),
        ("is_null", 1, 1, "scalar", "Whether a value is missing."),
        (
            "count",
            1,
            1,
            "aggregate",
            "Count non-NULL values at the Output Dimension; zero and False count.",
        ),
        (
            "count_distinct",
            1,
            1,
            "aggregate",
            "Count exact distinct non-NULL values at the Output Dimension.",
        ),
        (
            "count_if",
            1,
            1,
            "aggregate",
            "Count intersections with a true Boolean condition.",
        ),
        (
            "count_rows",
            0,
            0,
            "aggregate",
            "Count valid planning intersections at the Output Dimension.",
        ),
        (
            "attr",
            1,
            1,
            "reference",
            "Reference an available Dimension attribute.",
        ),
        (
            "grain",
            1,
            1,
            "reference",
            "Reference an output dimension grain column.",
        ),
    ]
)
FUNCTIONS = {
    item.name: (item.minimum, item.maximum)
    for item in FUNCTION_CATALOG
    if item.category == "scalar"
}
BY_NAME = {item.name: item for item in FUNCTION_CATALOG}
