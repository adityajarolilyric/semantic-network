from sqlglot import exp, parse_one
from ..warehouse.repository import quote_identifier as q
from .contracts import Check, ExecutionPlan, Pivot


class ClickHouseCompiler:
    """Owns dialect details. No client SQL or expression strings enter this compiler."""

    def __init__(self, database):
        self.database = database

    def table(self, name):
        return exp.Table(
            this=exp.to_identifier(name, quoted=True),
            db=exp.to_identifier(self.database, quoted=True),
        ).sql(dialect="clickhouse")

    @staticmethod
    def literal(value):
        if value is None:
            return "NULL"
        return exp.convert(value).sql(dialect="clickhouse")

    def compile(self, plan):
        scan = plan.root.source
        table = self.table(scan.table_name)
        names = {c["columnId"]: c["name"] for c in scan.columns}
        checks = []
        if not isinstance(plan.root, Pivot):
            sql = f"SELECT {', '.join(q(c['name']) for c in plan.schema.columns)} FROM {table}"
        else:
            stage = plan.root.stage
            keys = [q(names[k]) for k in stage["groupByColumnIds"]]
            selected, aggregates, guards = keys.copy(), [], []
            index = 0
            for pair in stage["pairs"]:
                attr, value = (
                    q(names[pair["attributeColumnId"]]),
                    q(names[pair["valueColumnId"]]),
                )
                conditions = []
                for output in pair["outputs"]:
                    condition = (
                        f"isNull({attr})"
                        if output["attributeValue"] is None
                        else f"{attr} = {self.literal(output['attributeValue'])}"
                    )
                    conditions.append(condition)
                    alias_name = f"__semantic_cells_{index}"
                    while alias_name in names.values() or alias_name in [
                        c["name"] for c in plan.schema.columns
                    ]:
                        index += 1
                        alias_name = f"__semantic_cells_{index}"
                    alias = q(alias_name)
                    index += 1
                    aggregates.append(
                        f"groupArrayIf(2)(tuple({value}), {condition}) AS {alias}"
                    )
                    typ = next(
                        c["physicalType"]
                        for c in plan.schema.columns
                        if c["columnId"] == output["columnId"]
                    )
                    # LowCardinality is an encoding wrapper, not a nullable scalar result.
                    if typ.startswith("LowCardinality("):
                        typ = typ[15:-1]
                    if not typ.startswith("Nullable("):
                        typ = f"Nullable({typ})"
                    selected.append(
                        f"if(empty({alias}), CAST(NULL AS {typ}), tupleElement({alias}[1], 1)) AS {q(output['name'])}"
                    )
                    guards.append(f"length({alias}) > 1")
                grouped = ", ".join([*keys, attr])
                checks.append(
                    Check(
                        "duplicate_pivot_cell",
                        f"SELECT {grouped}, count() AS occurrences FROM {table} WHERE {' OR '.join('(' + c + ')' for c in conditions)} GROUP BY {grouped} HAVING count() > 1 LIMIT 20",
                    )
                )
            inner = f"SELECT {', '.join([*keys, *aggregates])} FROM {table} GROUP BY {', '.join(keys)}"
            sql = f"SELECT {', '.join(selected)} FROM ({inner}) WHERE throwIf({' OR '.join(guards)}, 'SEMANTIC_DUPLICATE_PIVOT_CELL') = 0"
        # Persist a dialect AST, not a prebuilt SQL string as semantic metadata.
        ast = parse_one(sql, read="clickhouse")
        query = ast.sql(dialect="clickhouse")
        pk = plan.definition["primaryKeyColumnIds"]
        if pk:
            effective = {c["columnId"]: c["name"] for c in plan.schema.columns}
            keys = ", ".join(q(effective[k]) for k in pk)
            checks.append(
                Check(
                    "duplicate_managed_key",
                    f"SELECT {keys}, count() AS occurrences FROM ({query}) GROUP BY {keys} HAVING count() > 1 LIMIT 20",
                )
            )
        return ExecutionPlan(ast, tuple(checks), plan.schema)
