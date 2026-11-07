"""Execute warehouse checks and format stable, bounded semantic diagnostics."""

from dataclasses import dataclass

from ..core.errors import MetadataError
from ..warehouse.query_execution import WarehouseCheckRunner

PLANNING_CHECK_MESSAGES = {
    "null_planning_key": "Dimension grain keys cannot be NULL.",
    "duplicate_planning_key": "The same Dimension grain has conflicting output attributes. Select attributes determined by the grain or correct the source data.",
    "duplicate_pivot_cell": "Duplicate pivot cells must be corrected in the source data.",
    "conflicting_retained_values": "Retained columns must have one value per record key. Correct the source data or select the complete record keys.",
    "resolution_profiles_changed": "Source matching profiles changed during execution. Retry the build or preview.",
    "ambiguous_resolution_time_order": "First/Last requires calendar ordering values that distinguish dates within each native period.",
    "invalid_resolution_calendar": "The daily calendar contains an invalid date or a null/incompatible native period key.",
    "ambiguous_resolution_calendar": "A calendar date maps to more than one native period. Filter the Time dimension to an unambiguous calendar.",
    "invalid_resolution_range": "Start and End must be valid ISO dates, and Start must not be after End.",
    "resolution_calendar_coverage": "The date range extends outside the available Time dimension, or the calendar is empty.",
    "resolution_native_coverage": "A calendar date in the range maps to a missing native planning intersection. Rebuild or extend the planning population; ranges are not silently clipped.",
    "unmatched_resolution_range": "The date range contains no selected calendar dates.",
    "empty_self_weights": "Weighted by self has no populated values in this edited cell. Choose another disaggregation policy to initialise it.",
    "zero_self_weight_total": "Weighted by self cannot distribute this edit because its populated values total zero.",
    "invalid_edit_member": "A pivot member is incompatible with its dimension type. Refresh the view.",
    "empty_edit_scope": "This pivot cell has no eligible intersections at the measure's write level. No edit was applied.",
    "null_resolution_order": "Application order cannot be NULL. Choose a populated column with unique values for overlapping assignments.",
    "ambiguous_resolution_order": "Overlapping assignments have the same complete application order. Add a tie-breaker column.",
    "invalid_resolution_literal": "A matching value cannot be represented by the target attribute's data type.",
    "unmatched_resolution_row": "A transaction assignment matches no valid native planning intersection.",
    "resolution_expansion_limit": "Transaction resolution exceeds its configured expansion limit. Add more selective matching constraints.",
    "null_transaction_key": "Transaction grain keys cannot be NULL. Filter or correct the source rows.",
    "unmatched_transaction_grain": "A transaction key combination is absent from its native planning population.",
    "no_disaggregation_children": "A populated native measure cell has no matching Base rows.",
    "ambiguous_dimension_lookup": "The mapped dimension values have conflicting combinations of the selected attributes. Select attributes determined by the mapping; extra attributes do not change the planning grain.",
}


@dataclass(frozen=True)
class CheckDiagnostics:
    messages: dict[str, str]
    fallback_message: str
    row_limit: int | None = 20
    detail_length: int | None = 1000

    def raise_failure(self, code, rows, format_cell):
        details = []
        for row in rows[: self.row_limit]:
            message = str([format_cell(value) for value in row])
            details.append({"message": message[: self.detail_length]})
        raise MetadataError(
            code, self.messages.get(code, self.fallback_message), details=details
        )


__all__ = ["PLANNING_CHECK_MESSAGES", "CheckDiagnostics", "WarehouseCheckRunner"]
