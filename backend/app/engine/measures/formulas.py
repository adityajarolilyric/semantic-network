from ...core.errors import MetadataError
from ...formula_language.expressions import count_key, temporal_key
from ...formula_language.temporal import is_temporal
from .temporal import TemporalMeasureCompiler
from ...formula_language.sql import FormulaSqlCompiler
from ...warehouse.sql import quote_identifier as q
from ..sql.typed_formulas import attribute_alias, attribute_coordinate
from .contracts import COMPLETE, VALUE
from .counting import CountCompiler


class CalculatedMeasureCompiler:
    """Compile calculated expressions and their measure dependencies."""

    def __init__(self, compiler):
        self.compiler = compiler

    def compile_formula(self, node, measure, target, refs):
        return (
            FormulaSqlCompiler(self.compiler, measure, target, refs)
            .compile(node)
            .sql(dialect="clickhouse")
        )

    def compile_calculated_measure(self, measure, target):
        expression = measure["definition"]["formula"]["expression"]
        refs = {}
        attributes = {}
        counts = {}
        windows = {}

        def collect(node):
            if is_temporal(node):
                key = temporal_key(node)
                windows[key] = node
                refs.setdefault(key, "d" + str(len(refs)))
            elif node["type"] == "measureRef":
                refs.setdefault(node["measureId"], "d" + str(len(refs)))
            elif node["type"] == "count":
                key = count_key(node)
                counts[key] = node
                refs.setdefault(key, "d" + str(len(refs)))
            elif node["type"] == "attributeRef":
                attributes[attribute_alias(node)] = attribute_coordinate(
                    self.compiler, node, target
                )
            elif node["type"] == "binary":
                collect(node["left"])
                collect(node["right"])
            elif node["type"] == "call":
                for arg in node["arguments"]:
                    collect(arg)

        collect(expression)
        joins = []
        dependencies = {}
        for mid, alias in refs.items():
            if mid in windows:
                dep = TemporalMeasureCompiler(self.compiler).compile(
                    windows[mid], measure, target
                )
            elif mid in counts:
                count_cache_key = self.compiler.formula_count_cache_key(
                    mid, measure, target
                )
                if count_cache_key not in self.compiler.cache:
                    self.compiler.cache[count_cache_key] = CountCompiler(
                        self.compiler
                    ).compile(counts[mid], measure, target)
                dep = self.compiler.cache[count_cache_key]
            else:
                try:
                    dep = self.compiler.compile_measure_at_level(mid, target)
                except MetadataError as error:
                    if error.code not in {
                        "measure_path_unavailable",
                        "formula_grain_unavailable",
                    }:
                        raise
                    name = self.compiler.measures.get(mid, {}).get(
                        "name", "Missing measure"
                    )
                    grain = self.compiler.levels[target].get("name", "requested grain")
                    raise MetadataError(
                        error.code,
                        f'''"{measure["name"]}" requires "{name}" at "{grain}": {error.message}''',
                        details=error.details,
                    ) from error
            dependencies[mid] = dep
            joins.append(
                f"LEFT JOIN {q(dep)} {alias} ON "
                + self.compiler.join_keys("p", alias, target)
            )
        population = (
            self.compiler.temporal_population(measure, target)
            if windows or self.compiler.temporal_input_depth
            else self.compiler.formula_population(measure, target)
        )
        if attributes:
            seed = self.compiler.cte(
                f"SELECT {(', '.join((q(key) for key in self.compiler.keys(target))) + ', ' if self.compiler.keys(target) else '')}NULL AS {q(VALUE)} FROM ({population})"
            )
            population = self.compiler.enrich(seed, target, attributes)
        value = self.compiler.compile_formula(expression, measure, target, refs)
        flags = [
            f"coalesce({alias}.{q(COMPLETE)},{(1 if mid in counts else 0)})"
            for mid, alias in refs.items()
            if dependencies[mid] in self.compiler.coverage_nodes
        ]
        mixed_flags = [
            f"coalesce({alias}.{q(COMPLETE)},1)=2"
            for mid, alias in refs.items()
            if dependencies[mid] in self.compiler.mixed_nodes
        ]
        mixed = " OR ".join(mixed_flags) if mixed_flags else "0"
        flag = " AND ".join(flags) if flags else "1"
        if flags:
            value = f"if({flag},{value},NULL)"
        if mixed_flags:
            value = f"if({mixed},NULL,{value})"
        flag = f"if({mixed},toUInt8(2),toUInt8({flag}))"
        keys = ", ".join(
            ("p." + q(k) + " AS " + q(k) for k in self.compiler.keys(target))
        )
        keys = keys + ", " if keys else ""
        result = self.compiler.cte(
            f"SELECT {keys}{value} AS {q(VALUE)}, toUInt8({flag}) AS {q(COMPLETE)} FROM ({population}) p {' '.join(joins)}"
        )
        if flags:
            self.compiler.coverage_nodes[result] = True
        if mixed_flags or measure["definition"]["resultType"]["type"] in {
            "string",
            "date",
            "boolean",
        }:
            self.compiler.mixed_nodes.add(result)
        return result
