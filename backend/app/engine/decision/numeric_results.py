"""Compact output guards for known Float64 materialised Decision View values."""

from dataclasses import dataclass

from sqlglot import parse_one

from ...warehouse.sql import quote_identifier as q
from .relationships import TARGET


@dataclass(frozen=True)
class FloatResult:
    value: str
    overflow: str | None = None
    null: str | None = None


def float_result(expression, *, nullable, trusted=False, check_expression=None):
    """Stored values were validated on build; newly computed results are checked once.

    Read results retain full precision. Float64 conversion is necessary only
    when the SQL type can differ (calculated expressions or counting reducers).
    """
    if trusted:
        return FloatResult(f"toNullable({expression})" if nullable else expression)
    value = f"toNullable({expression})" if nullable else f"assumeNotNull({expression})"
    check = check_expression or expression
    return FloatResult(
        value,
        overflow=f"isNotNull({check}) AND NOT isFinite({check})",
        null=None if nullable else f"isNull({expression})",
    )


class MaterialisedResultProjection:
    """Convert computed outputs once, then validate/format shared result columns."""

    def __init__(self, host):
        self.host = host

    def compile(self, query, measure_ids, aliases, stages):
        host = self.host
        converted = {
            mid
            for mid in measure_ids
            if host.measures[mid]["definition"]["resultType"]["type"] == "number"
            and (
                mid not in host.view.leaves
                or any(
                    stage.method in {"count", "count_distinct"} for stage in stages[mid]
                )
            )
        }
        overflow_checks, null_checks = [], []
        selected = ["r." + q(key) + " AS " + q(key) for key in host.keys(TARGET)]
        for index, mid in enumerate(measure_ids):
            measure = host.measures[mid]
            value = "r." + q(aliases[mid])
            if mid in converted:
                value = f"toFloat64({value})"
            dtype = measure["definition"]["resultType"]
            if dtype["type"] == "number":
                result = float_result(
                    value,
                    nullable=dtype["nullable"],
                    trusted=mid in host.view.leaves and not stages[mid],
                    check_expression=q(f"value_{index}"),
                )
                value = result.value
                if result.overflow:
                    overflow_checks.append(result.overflow)
                if result.null:
                    null_checks.append(result.null)
            else:
                # Legacy Integer/Decimal measures retain their explicit bounds
                # and precision conversions; they are not known Float64 inputs.
                value, _ = host.cast_value(value, measure)
            selected.append(value + " AS " + q(f"value_{index}"))
        result = parse_one(
            f"SELECT {', '.join(selected)} FROM ({query.sql(dialect='clickhouse')}) r",
            read="clickhouse",
        )
        # These predicates never discard a valid row: throwIf returns zero or
        # raises. Sharing guards avoids a CASE wrapper around every output.
        for checks, error in (
            (overflow_checks, "SEMANTIC_MEASURE_OVERFLOW"),
            (null_checks, "SEMANTIC_MEASURE_NULL"),
        ):
            if checks:
                condition = " OR ".join(f"({check})" for check in checks)
                result = result.where(
                    parse_one(f"throwIf({condition},'{error}')=0", read="clickhouse")
                )
        return result
