"""Canonical function registry shared by language discovery and semantic typing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Function:
    name: str
    minimum: int
    maximum: int
    category: str
    description: str

    @property
    def group(self):
        if self.category == "temporal":
            return "Time series"
        if self.category == "aggregate":
            return "Counting"
        if self.name in {"if", "coalesce", "is_null"}:
            return "Conditional"
        if self.name in {"and", "or", "not"}:
            return "Logical"
        if self.name in {"concat", "upper", "lower", "trim"}:
            return "Text"
        if self.name in {"date", "add_days", "days_between"}:
            return "Date"
        return "Arithmetic"


FUNCTION_CATALOG = tuple(
    Function(*row)
    for row in [
        ("abs", 1, 1, "scalar", "Absolute numeric value."),
        ("sqrt", 1, 1, "scalar", "Square root; negative inputs return NULL."),
        (
            "root",
            2,
            2,
            "scalar",
            "Nth root; degree must be a positive whole number. Invalid domains return NULL.",
        ),
        (
            "power",
            2,
            2,
            "scalar",
            "Raise a number to a power; non-finite results return NULL.",
        ),
        ("ceil", 1, 1, "scalar", "Round up to the nearest whole number."),
        ("floor", 1, 1, "scalar", "Round down to the nearest whole number."),
        (
            "rolling",
            4,
            4,
            "temporal",
            'ROLLING(value, "SUM", start_offset, end_offset). Inclusive calendar-period offsets; CalculateFirst only.',
        ),
        (
            "cumulative",
            2,
            2,
            "temporal",
            'CUMULATIVE(value, "SUM"). From the start of each series/reset group; CalculateFirst only.',
        ),
        (
            "lag",
            2,
            2,
            "temporal",
            "Value a positive number of calendar periods earlier; CalculateFirst only.",
        ),
        (
            "lead",
            2,
            2,
            "temporal",
            "Value a positive number of calendar periods later; CalculateFirst only.",
        ),
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
    if item.category in {"scalar", "temporal"}
}
BY_NAME = {item.name: item for item in FUNCTION_CATALOG}
