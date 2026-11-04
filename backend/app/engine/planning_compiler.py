from .table_storage import GENERATION
from ..warehouse.repository import quote_literal as lit
from sqlglot import parse_one

from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q
from .compiler import ClickHouseCompiler
from .contracts import (
    Check,
    CrossJoin,
    DistinctProject,
    ExecutionPlan,
    Filter,
    LookupJoin,
    RelationScan,
)


class PlanningLevelCompiler(ClickHouseCompiler):
    def filter_sql(self, definition, available):
        from .filters import FilterCompiler

        return FilterCompiler().compile(definition, available)

    def reduced_lookup(self, sql, keys, attributes):
        """Membership or functional enrichment, independent of raw member count."""
        ids = list(dict.fromkeys([*keys, *attributes]))
        return f"SELECT DISTINCT {', '.join(q(cid) for cid in ids)} FROM ({sql})"

    def lookup_sql(self, join):
        sql, _ = self.render(join.lookup)
        return self.reduced_lookup(
            sql, [cid for _, cid in join.mappings], [cid for _, cid in join.outputs]
        )

    def render(self, node):
        if isinstance(node, RelationScan):
            table = self.table(node.table_name)
            if node.input_pipeline:
                from .population_inputs import compile_population_input

                query, checks = compile_population_input(
                    self.database, node.input_pipeline
                )
                self.input_checks.extend(
                    c for c in checks if c not in self.input_checks
                )
                table = "(" + query + ")"
            if node.transform:
                compiled = ClickHouseCompiler(self.database).compile(node.transform)
                for check in compiled.checks:
                    if check not in self.input_checks:
                        self.input_checks.append(check)
                table = "(" + compiled.sql + ")"
            sql = f"SELECT {', '.join((f'assumeNotNull({q(name)})' if cid in node.nonnullable_ids else q(name)) + ' AS ' + q(cid) for cid, name in node.columns)} FROM {table}"
            if node.generation:
                sql += f" WHERE {q(GENERATION)}={lit(node.generation)}"
            ids = [cid for cid, _ in node.columns]
            if node.filters:
                sql = f"SELECT * FROM ({sql}) WHERE {self.filter_sql(node.filters, node.filter_columns or ids)}"
            return sql, ids
        if isinstance(node, CrossJoin):
            queries = [self.render(f) for f in node.factors]
            selected = [
                f"f{i}.{q(cid)} AS {q(cid)}"
                for i, (_, ids) in enumerate(queries)
                for cid in ids
            ]
            return "SELECT " + ", ".join(selected) + " FROM " + " CROSS JOIN ".join(
                f"({sql}) AS f{i}" for i, (sql, _) in enumerate(queries)
            ), [cid for _, ids in queries for cid in ids]
        if isinstance(node, DistinctProject):
            query, ids = self.render(node.source)
            return f"SELECT DISTINCT * FROM ({query})", ids
        if isinstance(node, LookupJoin):
            left, ids = self.render(node.source)
            right = self.lookup_sql(node)
            selected = [f"p.{q(cid)} AS {q(cid)}" for cid in ids]
            selected += [f"d.{q(cid)} AS {q(out)}" for out, cid in node.outputs]
            selected.append(f"coalesce(d.__lookup_match, 0) AS {q(node.match_id)}")
            on = " AND ".join(
                f"p.{q(left_id)} = d.{q(right_id)}"
                for left_id, right_id in node.mappings
            )
            return (
                f"SELECT {', '.join(selected)} FROM ({left}) AS p LEFT JOIN (SELECT *, toUInt8(1) AS __lookup_match FROM ({right})) AS d ON {on}",
                [*ids, *(out for out, _ in node.outputs), node.match_id],
            )
        if isinstance(node, Filter):
            query, ids = self.render(node.source)
            return (
                f"SELECT * FROM ({query}) WHERE {self.filter_sql(node.definition, node.columns or ids)}",
                ids,
            )
        raise MetadataError(
            "unsupported_operator", "This planning operator is not supported."
        )

    def compile(self, plan):
        self.input_checks = []
        query, _ = self.render(plan.root)
        checks = []
        for join in plan.joins:
            if not join.outputs:
                continue  # DISTINCT mapping keys implement a pure existence check.
            right = self.lookup_sql(join)
            keys = ", ".join(q(cid) for _, cid in join.mappings)
            left, _ = self.render(join.source)
            left_keys = ", ".join(q(cid) for cid, _ in join.mappings)
            on = " AND ".join(
                f"p.{q(left_id)} = d.{q(right_id)}"
                for left_id, right_id in join.mappings
            )
            checks.append(
                Check(
                    "ambiguous_dimension_lookup",
                    f"SELECT DISTINCT d.* FROM (SELECT {keys}, count() FROM ({right}) GROUP BY {keys} HAVING count()>1) d INNER JOIN (SELECT DISTINCT {left_keys} FROM ({left})) p ON {on} LIMIT 20",
                )
            )
        if plan.joins:
            unmatched = " OR ".join(q(j.match_id) + " = 0" for j in plan.joins)
            checks.append(
                Check(
                    "unmatched_dimension_member",
                    f"SELECT * FROM ({query}) WHERE {unmatched} LIMIT 20",
                )
            )
            query = f"SELECT * FROM ({query}) WHERE throwIf({unmatched}, 'SEMANTIC_UNMATCHED_DIMENSION') = 0"
        selected = ", ".join(
            q(c["columnId"]) + " AS " + q(c["name"]) for c in plan.schema.columns
        )
        ast = parse_one(f"SELECT DISTINCT {selected} FROM ({query})", read="clickhouse")
        return ExecutionPlan(
            ast,
            tuple([*self.input_checks, *checks]),
            plan.schema,
            "planning-v4-plain-output-names-sqlglot-27.28.1",
        )
