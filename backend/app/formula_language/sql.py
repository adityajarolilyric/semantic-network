"""Lower the semantic scalar AST to SQLGlot nodes, independent of source SQL syntax.

The relational planner supplies resolved columns; numeric/Decimal division retains the engine NULL and precision rules. Function
names come exclusively from the registry, never from user-provided SQL.
"""

from sqlglot import exp

from ..core.errors import MetadataError
from ..engine.measures.contracts import COMPLETE, VALUE
from ..engine.sql.typed_formulas import attribute_alias
from .expressions import count_key, temporal_key
from .temporal import is_temporal
from .arithmetic import compile_arithmetic
from .sql_nodes import function


class FormulaSqlCompiler:
    def __init__(self, host, measure, target, references):
        self.host, self.measure, self.target, self.references = (
            host,
            measure,
            target,
            references,
        )

    def column(self, name, table):
        return exp.column(name, table=table, quoted=True)

    def compile(self, node):
        if is_temporal(node):
            return self.column(VALUE, self.references[temporal_key(node)])
        kind = node["type"]
        if kind == "literal":
            return exp.convert(node["value"])
        if kind in {"measureRef", "count"}:
            key = node["measureId"] if kind == "measureRef" else count_key(node)

            value = self.column(VALUE, self.references[key])
            if kind == "count":
                # An absent group is a known zero; a present unknown/Mixed count stays NULL.
                return function(
                    "if",
                    function("isNull", self.column(COMPLETE, self.references[key])),
                    exp.convert(0),
                    value,
                )
            return value
        if kind == "attributeRef":
            return self.column(attribute_alias(node), "p")
        if kind == "grainRef":
            coordinate = self.host.relationships.outputs(
                self.measure["definition"]["nativeGrainId"]
            ).get(node["grainColumnId"])
            resolved = (
                self.host.relationships.resolve(self.target, coordinate)
                if coordinate
                else None
            )
            if not resolved or not resolved.column_id:
                raise MetadataError(
                    "formula_grain_unavailable",
                    "A formula grain reference is unavailable at the requested level.",
                )
            return self.column(resolved.column_id, "p")
        if kind == "binary":
            left, right = self.compile(node["left"]), self.compile(node["right"])
            if node["operator"] == "divide":
                dtype = self.measure["definition"]["resultType"]
                if dtype["type"] == "decimal":
                    scale = min(60, max(18, dtype.get("scale") or 0))
                    return function(
                        "divideDecimal",
                        function("toDecimal256", left, exp.convert(scale)),
                        function(
                            "nullIf",
                            function("toDecimal256", right, exp.convert(scale)),
                            exp.convert(0),
                        ),
                        exp.convert(scale),
                    )
                return exp.Paren(
                    this=exp.Div(
                        this=left, expression=function("nullIf", right, exp.convert(0))
                    )
                )
            operator = {
                "add": exp.Add,
                "subtract": exp.Sub,
                "multiply": exp.Mul,
                "eq": exp.EQ,
                "ne": exp.NEQ,
                "gt": exp.GT,
                "gte": exp.GTE,
                "lt": exp.LT,
                "lte": exp.LTE,
            }[node["operator"]]
            return exp.Paren(this=operator(this=left, expression=right))
        args = [self.compile(arg) for arg in node["arguments"]]
        fn = node["function"]
        null = exp.Null()
        if fn == "if":
            return function(
                "if", function("isNull", args[0].copy()), null, function("if", *args)
            )
        if fn == "days_between":
            return function("dateDiff", exp.Literal.string("day"), *args)
        if fn == "date":
            parsed = function("toDate32OrNull", args[0].copy())
            invalid = exp.and_(
                function("isNotNull", args[0].copy()),
                exp.or_(
                    function("isNull", parsed.copy()),
                    exp.NEQ(
                        this=function("toString", parsed.copy()),
                        expression=args[0].copy(),
                    ),
                ),
            )
            guard = function(
                "throwIf", invalid, exp.Literal.string("SEMANTIC_INVALID_DATE")
            )
            return function(
                "if", exp.EQ(this=guard, expression=exp.convert(0)), parsed, null
            )
        if fn == "add_days":
            days = args[1]
            invalid = exp.and_(
                function("isNotNull", days.copy()),
                exp.or_(
                    exp.NEQ(
                        this=days.copy(), expression=function("trunc", days.copy())
                    ),
                    function(
                        "isNull",
                        function(
                            "accurateCastOrNull",
                            days.copy(),
                            exp.Literal.string("Int64"),
                        ),
                    ),
                ),
            )
            guard = function(
                "throwIf", invalid, exp.Literal.string("SEMANTIC_DATE_INTEGER_DAYS")
            )
            return function(
                "if",
                exp.EQ(this=guard, expression=exp.convert(0)),
                function("addDays", args[0], function("toInt64", days.copy())),
                null,
            )
        if fn in {"sqrt", "root", "power"}:
            return compile_arithmetic(fn, args)
        names = {
            "ceil": "ceil",
            "floor": "floor",
            "abs": "abs",
            "round": "roundBankers",
            "coalesce": "coalesce",
            "min": "least",
            "max": "greatest",
            "concat": "concat",
            "upper": "upperUTF8",
            "lower": "lowerUTF8",
            "trim": "trimBoth",
            "is_null": "isNull",
            "not": "not",
            "and": "and",
            "or": "or",
        }
        value = function(names[fn], *[arg.copy() for arg in args])
        if fn in {"min", "max"}:
            value = function(
                "if", exp.or_(*[function("isNull", arg) for arg in args]), null, value
            )
        return value
