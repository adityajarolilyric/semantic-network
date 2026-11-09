"""Temporal formulas have an explicit, fixed evaluation calendar and series."""

from ..core.errors import MetadataError
from ..domain.typed_values import result_type, NUMERIC
from ..formula_language.expressions import contains_temporal, contains_counts, walk
from ..formula_language.temporal import is_temporal, window_spec


def validate_temporal(context, owner, level):
    definition = owner["definition"]
    expression = definition["formula"]["expression"]
    active = contains_temporal(expression)
    temporal = definition.get("temporal")

    def error(message):
        context.error(owner, "temporal_context", message, "definition.temporal")

    if not active:
        if temporal:
            error("Time-series settings require a temporal function in the formula.")
        return
    if definition.get("calculationType") != "CalculateFirst":
        error(
            "Time-series formulas require CalculateFirst; aggregate their stored results using the measure policies."
        )
    if contains_counts(expression):
        error(
            "Intersection counts and temporal functions have different calculation grains. Create a separate count measure first."
        )
    if not temporal or not level:
        error(
            "Choose a time key, calendar ordering column and series keys for this time-series formula."
        )
        return
    keys = set(level["definition"]["grain"]["keyIds"])
    outputs = context.dimensions_rules
    if temporal["timeKeyId"] not in keys or not set(temporal["partitionColumnIds"]) <= (
        keys - {temporal["timeKeyId"]}
    ):
        error("The time key and series keys must belong to the Output Dimension grain.")
    physical = outputs.grain_type(level, temporal["orderColumnId"])
    dtype = result_type(physical or "")
    expected_types = NUMERIC if temporal["periodUnit"] == "index" else {"date"}
    if not dtype or dtype["type"] not in expected_types:
        error(
            "Use a Date column for day/week/month periods, or a numeric consecutive calendar index for index periods. Text week labels cannot define chronological order."
        )
    if temporal.get("resetColumnId") and not outputs.grain_type(
        level, temporal["resetColumnId"]
    ):
        error("The reset column must be available in the Output Dimension.")
    try:
        for node in walk(expression):
            if is_temporal(node):
                window_spec(node)
    except MetadataError as exc:
        error(exc.message)
