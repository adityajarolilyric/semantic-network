"""Shared policy-driven aggregation stages for stored values and Decision View."""

from dataclasses import dataclass

from sqlglot import exp, parse_one

from ...core.errors import MetadataError
from ...warehouse.sql import quote_identifier as q
from .contracts import COMPLETE, VALUE
from .value_states import mixed_group


@dataclass(frozen=True)
class AggregationStage:
    method: str
    group_keys: tuple[str, ...]
    carry_time_order: bool


@dataclass(frozen=True)
class AggregationPlan:
    target_keys: tuple[str, ...]
    time_keys: tuple[str, ...]
    non_time_keys: tuple[str, ...]
    remove_time: bool
    remove_non_time: bool
    policy: dict
    has_time_order: bool = False

    def stages(self):
        pending = {"time": self.remove_time, "nonTime": self.remove_non_time}
        order = (
            ("nonTime", "time")
            if self.policy.get("order", "nonTimeThenTime") == "nonTimeThenTime"
            else ("time", "nonTime")
        )
        result = []
        for axis in order:
            if not pending[axis]:
                continue
            pending[axis] = False
            other = "time" if axis == "nonTime" else "nonTime"
            extra = (
                (self.time_keys if other == "time" else self.non_time_keys)
                if pending[other]
                else ()
            )
            result.append(
                AggregationStage(
                    self.policy[axis],
                    tuple(dict.fromkeys((*self.target_keys, *extra))),
                    bool(self.has_time_order and axis == "nonTime" and pending["time"]),
                )
            )
        return tuple(result)


@dataclass(frozen=True)
class AggregationResult:
    sql: str
    has_mixed: bool


class AggregationOperator:
    @staticmethod
    def compile(host, source_sql, plan: AggregationPlan, has_mixed=False):
        stages = plan.stages()
        if not stages:
            return AggregationResult(source_sql, has_mixed)
        query = parse_one(source_sql, read="clickhouse")
        for stage in stages:
            ordering = q("__time_order") if plan.has_time_order else None
            value = host.aggregate(
                "aggregation_input." + q(VALUE),
                stage.method,
                plan.policy,
                ordering,
                "aggregation_input." + q(COMPLETE) if has_mixed else None,
            )
            conflict = (
                mixed_group(
                    stage.method,
                    "aggregation_input." + q(VALUE),
                    "aggregation_input." + q(COMPLETE),
                    ordering,
                )
                if has_mixed or stage.method == "single_value"
                else "0"
            )
            has_mixed = has_mixed or stage.method == "single_value"
            expressions = [exp.column(key, quoted=True) for key in stage.group_keys]
            expressions.extend(
                [
                    parse_one(
                        f"if(({conflict}) OR min(aggregation_input.{q(COMPLETE)})=0,NULL,{value})",
                        read="clickhouse",
                    ).as_(VALUE, quoted=True),
                    parse_one(
                        f"if({conflict},toUInt8(2),min(aggregation_input.{q(COMPLETE)}))",
                        read="clickhouse",
                    ).as_(COMPLETE, quoted=True),
                ]
            )
            if stage.carry_time_order:
                expressions.append(
                    exp.Min(this=exp.column("__time_order", quoted=True)).as_(
                        "__time_order", quoted=True
                    )
                )
            query = exp.select(*expressions).from_(
                exp.Subquery(
                    this=query,
                    alias=exp.TableAlias(this=exp.to_identifier("aggregation_input")),
                )
            )
            if stage.group_keys:
                query = query.group_by(
                    *(exp.column(key, quoted=True) for key in stage.group_keys)
                )
        return AggregationResult(query.sql(dialect="clickhouse"), has_mixed)


def aggregate_expression(expression, method, policy, ordering=None, state=None):
    if method in ("first", "last"):
        if not ordering:
            raise MetadataError(
                "time_order_required",
                "First/last aggregation requires an explicit Time ordering mapping.",
            )
        function = "argMinIf" if method == "first" else "argMaxIf"
        eligible = f"isNotNull({expression})" + (f" OR ({state})=2" if state else "")
        inner = (
            f"tupleElement({function}(tuple({expression}), {ordering}, {eligible}),1)"
        )
    elif method == "single_value":
        inner = f"singleValueOrNull({expression})"
    elif method in {"any_true", "all_true"}:
        inner = f"{('max' if method == 'any_true' else 'min')}({expression})"
    elif method == "count_distinct":
        inner = f"uniqExact({expression})"
    elif method == "count":
        inner = f"count({expression})"
    else:
        inner = f"{method}({expression})"
    empty = "0" if policy["allNullResult"] == "zero" else "NULL"
    eligible_count = f"isNotNull({expression})" + (
        f" OR ({state})=2" if state and method in {"first", "last"} else ""
    )
    result = f"if(countIf({eligible_count})=0,{empty},{inner})"
    if policy["nullInputPolicy"] == "propagate":
        null = f"isNull({expression})" + (f" AND ({state})!=2" if state else "")
        result = f"if(countIf({null})>0,NULL,{result})"
    return result
