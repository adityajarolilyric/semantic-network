from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...sql.contracts import digest

RESOLUTION_POLICY = "transaction-resolution-v3-calendar-stage"


def key_id(transaction_id, grain_id):
    return "txkey_" + digest([transaction_id, grain_id])[:24]


if TYPE_CHECKING:
    from ..dates.ranges import DateRangeCompiler


@dataclass
class ResolvedTransaction:
    node: str
    columns: list
    mappings: list
    matches: str
    latest_nodes: list
    input_node: str | None = None
    unfiltered_node: str | None = None


VALUE_NAME = "__value"


@dataclass(frozen=True)
class PreparedResolutionInput:
    raw_node: str
    profile_node: str
    population_node: str
    payload_column_ids: list[str]
    order_column_ids: list[str]
    partition_column_ids: list[str]
    payload_expression: str
    order_expression: str
    calendar_enabled: bool
    date_range: "DateRangeCompiler"
