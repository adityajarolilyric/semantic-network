"""Expand changed input keys into the periods affected by each window."""

from ...formula_language.temporal import is_temporal, window_spec
from ...warehouse.sql import quote_identifier as q
from ..measures.temporal import partition_columns, period_expression


class TemporalEditImpact:
    def __init__(self, impact):
        self.impact = impact
        self.compiler = impact.compiler

    def expression_scope(self, node, level, context):
        if node["type"] == "measureRef":
            return self.impact.scope_for(node["measureId"], level)
        if node["type"] == "binary":
            return self.impact.union_keys(
                [
                    self.expression_scope(node[k], level, context)
                    for k in ("left", "right")
                ],
                level,
            )
        if node["type"] != "call":
            return None
        if is_temporal(node):
            changed = self.expression_scope(node["arguments"][0], level, context)
            return self.expand(changed, node, level, context) if changed else None
        return self.impact.union_keys(
            [self.expression_scope(arg, level, context) for arg in node["arguments"]],
            level,
        )

    def series_population(self, population, scope, level, context):
        """Read full history, but only for series/reset groups with affected cells."""
        partitions = partition_columns(context)
        if not partitions:
            return population
        c = self.compiler
        members = ", ".join("s." + q(k) for k in partitions)
        affected = f"SELECT DISTINCT {members} FROM ({population}) s INNER JOIN {q(scope)} k ON {c.join_keys('s', 'k', level)}"
        equality = " AND ".join(
            f"isNotDistinctFrom(p.{q(k)},s.{q(k)})" for k in partitions
        )
        return (
            f"SELECT p.* FROM ({population}) p INNER JOIN ({affected}) s ON {equality}"
        )

    def expand(self, scope, node, level, context):
        c = self.compiler
        population = c.population(level)
        source = f"SELECT p.* FROM ({population}) p INNER JOIN {q(scope)} s ON {c.join_keys('p', 's', level)}"
        _, start, end = window_spec(node)
        # Input i affects output j when j+start <= i <= j+end.
        source_order, output_order = (
            period_expression(context, "s"),
            period_expression(context, "p"),
        )
        partitions = partition_columns(context)
        equality = [f"isNotDistinctFrom(p.{q(k)},s.{q(k)})" for k in partitions]
        join = (
            (f"INNER JOIN ({source}) s ON " + " AND ".join(equality))
            if equality
            else f"CROSS JOIN ({source}) s"
        )
        conditions = []
        conditions.append(f"{output_order}>={source_order}-({end})")
        if start is not None:
            conditions.append(f"{output_order}<={source_order}-({start})")
        # Hash join by series first; range bounds stay in WHERE for CH 25.3.
        keys = ", ".join(f"p.{q(k)} AS {q(k)}" for k in c.keys(level))
        return c.cte(
            f"SELECT DISTINCT {keys} FROM ({population}) p {join} WHERE {' AND '.join(conditions)}"
        )
