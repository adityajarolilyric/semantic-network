"""Aggregate expression arguments at an explicit population grain, never cached counts."""

from sqlglot import exp, parse_one

from ..core.errors import MetadataError
from ..formula_language.sql import function
from .measure_compiler import COMPLETE, VALUE
from ..warehouse.repository import quote_identifier as q


class CountCompiler:
    def __init__(self, host):
        self.host = host

    def compile(self, node, measure, target):
        host = self.host
        source = measure["definition"]["nativePlanningLevelId"]
        if not source or host.relationships.analyze(source, target)[
            "relationship"
        ] not in {"same", "equivalent", "roll_up"}:
            raise MetadataError(
                "measure_path_unavailable",
                "the measure’s Output Planning Level must determine the requested grain; count results cannot be distributed.",
            )
        if node["function"] == "count_rows":
            query = host.cte(
                f"SELECT {', '.join(q(k) for k in host.keys(source))}, 1 AS {q(VALUE)}, toUInt8(1) AS {q(COMPLETE)} FROM ({host.population(source)})"
            )
        else:
            argument_measure = {
                **measure,
                "definition": {
                    **measure["definition"],
                    "nativePlanningLevelId": source,
                    "resultType": {"type": "number", "nullable": True},
                    "formula": {"expression": node["arguments"][0]},
                },
            }
            query = host.compile_calculated_measure(argument_measure, source)
        coordinates = {
            "__count_" + key: value
            for key, value in host.relationships.mapping(source, target).items()
        }
        data = host.enrich(query, source, coordinates)

        def column(name):
            return exp.column(name, table="count_input", quoted=True)

        value, state = column(VALUE), column(COMPLETE)
        aggregate = {
            "count": lambda: exp.Count(this=value.copy()),
            "count_distinct": lambda: function("uniqExact", value.copy()),
            "count_if": lambda: function("countIf", value.copy()),
            "count_rows": lambda: exp.Count(this=exp.Star()),
        }[node["function"]]()
        mixed = function(
            "countIf", exp.EQ(this=state.copy(), expression=exp.convert(2))
        )
        missing = function(
            "countIf", exp.EQ(this=state.copy(), expression=exp.convert(0))
        )
        flag = function(
            "if",
            exp.GT(this=mixed, expression=exp.convert(0)),
            exp.convert(2),
            function(
                "if",
                exp.GT(this=missing, expression=exp.convert(0)),
                exp.convert(0),
                exp.convert(1),
            ),
        )
        selected = [
            exp.alias_(column("__count_" + key), key, quoted=True)
            for key in host.keys(target)
        ]
        selected += [
            exp.alias_(
                function(
                    "if",
                    exp.EQ(this=flag.copy(), expression=exp.convert(1)),
                    aggregate,
                    exp.Null(),
                ),
                VALUE,
                quoted=True,
            ),
            exp.alias_(function("toUInt8", flag), COMPLETE, quoted=True),
        ]
        grouped = exp.select(*selected).from_(
            parse_one(data, read="clickhouse").subquery("count_input")
        )
        if coordinates:
            grouped = grouped.group_by(*[column(name) for name in coordinates])
        grouped_node = host.cte(grouped.sql(dialect="clickhouse"))
        host.coverage_nodes[grouped_node] = True
        host.mixed_nodes.add(grouped_node)
        return grouped_node
