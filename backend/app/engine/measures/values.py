from copy import deepcopy

from ...warehouse.sql import quote_identifier as q
from .contracts import COMPLETE, VALUE


class MeasureValueCompiler:
    """Apply typed casts, NULL guards and six-place value quantisation."""

    def __init__(self, compiler):
        self.compiler = compiler

    def widen(self, expression, measure):
        dtype = measure["definition"]["resultType"]
        if dtype["type"] in {"string", "date", "boolean"}:
            return expression
        if dtype["type"] == "integer":
            return f"toInt256({expression})"
        if dtype["type"] == "decimal":
            return f"toDecimal256({expression}, {dtype['scale']})"
        return f"toFloat64({expression})"

    def divide(self, left, right, measure):
        if measure["definition"]["resultType"]["type"] != "decimal":
            return f"({left}) / nullIf(({right}),0)"
        scale = min(60, max(18, measure["definition"]["resultType"].get("scale") or 0))
        return f"divideDecimal(toDecimal256({left},{scale}), nullIf(toDecimal256({right},{scale}),0), {scale})"

    def rounded_value(self, expression, measure):
        dtype = measure["definition"]["resultType"]
        if dtype["type"] in {"string", "date", "boolean"}:
            return expression
        if dtype["type"] == "decimal":
            return f"roundBankers({expression},{min(6, dtype['scale'])})"
        number = f"toFloat64({expression})"
        return f"if(abs({number})>=1e15,{number},roundBankers({number},6))"

    def quantize_node(self, query, measure, level):
        """Downstream representations use the same six-place values we store."""
        selected = ["r." + q(k) + " AS " + q(k) for k in self.compiler.keys(level)]
        selected.append(
            self.compiler.rounded_value("r." + q(VALUE), measure) + " AS " + q(VALUE)
        )
        flag = (
            "r." + q(COMPLETE)
            if query in self.compiler.coverage_nodes
            or query in self.compiler.mixed_nodes
            else "toUInt8(1)"
        )
        selected.append(flag + " AS " + q(COMPLETE))
        result = self.compiler.cte(f"SELECT {', '.join(selected)} FROM {q(query)} r")
        if query in self.compiler.coverage_nodes:
            self.compiler.coverage_nodes[result] = True
        if query in self.compiler.mixed_nodes:
            self.compiler.mixed_nodes.add(result)
        return result

    def cast_value(self, expression, measure):
        dtype = measure["definition"]["resultType"]
        if dtype["type"] in {"string", "date", "boolean"}:
            typ = {"string": "String", "date": "Date32", "boolean": "Bool"}[
                dtype["type"]
            ]
            converted = f"accurateCastOrNull({expression},{self.compiler.literal(typ)})"
            guard = f"throwIf(isNotNull({expression}) AND isNull({converted}),'SEMANTIC_MEASURE_OVERFLOW')"
            if not dtype["nullable"]:
                guard += f" + throwIf(isNull({expression}),'SEMANTIC_MEASURE_NULL')"
            return (f"if(({guard})=0,{converted},NULL)", typ)
        if dtype["type"] == "decimal":
            typ = f"Decimal({dtype['precision']},{dtype['scale']})"
            rounded = self.compiler.rounded_value(expression, measure)
            parsed = (
                f"accurateCastOrNull(toString({rounded}),{self.compiler.literal(typ)})"
            )
            integer_digits = dtype["precision"] - dtype["scale"]
            maximum = ("9" * integer_digits or "0") + (
                "." + "9" * dtype["scale"] if dtype["scale"] else ""
            )
            bound = f"toDecimal256({self.compiler.literal(maximum)},{dtype['scale']})"
            converted = (
                f"if(({parsed}) > {bound} OR ({parsed}) < -{bound},NULL,{parsed})"
            )
        elif dtype["type"] == "integer":
            typ = "Int64"
            converted = f"accurateCastOrNull({expression},'Int64')"
            converted = f"if(({expression})!=trunc({expression}),NULL,{converted})"
        else:
            typ = "Float64"
            converted = f"if(isFinite(toFloat64({expression})),{self.compiler.rounded_value(expression, measure)},NULL)"
        guard = f"throwIf(isNotNull({expression}) AND isNull({converted}),'SEMANTIC_MEASURE_OVERFLOW')"
        if not dtype["nullable"]:
            guard += f" + throwIf(isNull({expression}),'SEMANTIC_MEASURE_NULL')"
        expression = f"if(({guard})=0,{converted},NULL)"
        if not dtype["nullable"]:
            expression = f"if(({guard})=0,assumeNotNull({converted}),CAST(0 AS {typ}))"
        return (expression, typ)

    def cast_node_value(self, alias, node, measure):
        expression = alias + "." + q(VALUE)
        if (
            node not in self.compiler.coverage_nodes
            and node not in self.compiler.mixed_nodes
        ):
            return self.compiler.cast_value(expression, measure)
        nullable = deepcopy(measure)
        nullable["definition"]["resultType"]["nullable"] = True
        value, typ = self.compiler.cast_value(expression, nullable)
        if not measure["definition"]["resultType"]["nullable"]:
            guard = f"throwIf(coalesce({alias}.{q(COMPLETE)},0)=1 AND isNull({expression}),'SEMANTIC_MEASURE_NULL')"
            value = f"if(({guard})=0,{value},NULL)"
        return (value, typ)
