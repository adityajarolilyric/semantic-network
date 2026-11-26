"""Domain-safe arithmetic lowering, independent of relational measure planning."""

from sqlglot import exp
from .sql_nodes import function


def compile_arithmetic(fn, args):
    null = exp.Null()
    number = args[0]
    if fn == "sqrt":
        result = function("sqrt", function("greatest", number.copy(), exp.convert(0)))
        valid = exp.GTE(this=number.copy(), expression=exp.convert(0))
    elif fn == "power":
        result = function("pow", number.copy(), args[1].copy())
        valid = function("isFinite", result.copy())
    else:
        degree = args[1]
        valid = exp.and_(
            exp.GT(this=degree.copy(), expression=exp.convert(0)),
            exp.EQ(this=degree.copy(), expression=function("trunc", degree.copy())),
            exp.or_(
                exp.GTE(this=number.copy(), expression=exp.convert(0)),
                exp.EQ(
                    this=function("modulo", degree.copy(), exp.convert(2)),
                    expression=exp.convert(1),
                ),
            ),
        )
        exponent = exp.Div(
            this=exp.convert(1),
            expression=function("nullIf", degree.copy(), exp.convert(0)),
        )
        magnitude = function("pow", function("abs", number.copy()), exponent)
        result = function(
            "if",
            exp.LT(this=number.copy(), expression=exp.convert(0)),
            exp.Neg(this=magnitude.copy()),
            magnitude,
        )
    return function(
        "if", exp.and_(valid, function("isFinite", result.copy())), result, null
    )
