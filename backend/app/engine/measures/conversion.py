"""Apply stored, grain-compatible conversion factors to referenced values."""

from ...warehouse.sql import quote_identifier as q
from ..sql.contracts import Check
from .contracts import COMPLETE, VALUE


class MeasureConversionCompiler:
    def __init__(self, compiler):
        self.compiler = compiler

    def apply(self, measure, source):
        definition = measure["definition"]
        conversion = definition.get("conversion")
        if not conversion:
            return source
        c = self.compiler
        level = definition["nativeGrainId"]
        factor = c.compile_measure_at_level(conversion["factorMeasureId"], level)
        value, rate = f"v.{q(VALUE)}", f"f.{q(VALUE)}"
        known = (
            f"coalesce(f.{q(COMPLETE)},0)"
            if factor in c.coverage_nodes or factor in c.mixed_nodes
            else "1"
        )
        joined = f"FROM {q(source)} v LEFT JOIN {q(factor)} f ON {c.join_keys('v', 'f', level)}"
        c.assertions.append(
            Check(
                "invalid_conversion_factor",
                f"SELECT {', '.join('v.' + q(k) for k in c.keys(level))} {joined} WHERE isNotNull({value}) AND (isNull({rate}) OR NOT isFinite({rate}) OR {rate}<=0 OR {known}!=1) LIMIT 20",
            )
        )
        converted = (
            f"({value} * {rate})"
            if conversion["operation"] == "multiply"
            else f"({value} / nullIf({rate},0))"
        )
        keys = ", ".join(f"v.{q(k)} AS {q(k)}" for k in c.keys(level))
        return c.cte(
            f"SELECT {keys},{converted} AS {q(VALUE)},v.{q(COMPLETE)} AS {q(COMPLETE)} {joined}"
        )
