from sqlglot import exp, parse_one

from ..warehouse.sql import quote_identifier as q
from .contracts import Check, ExecutionPlan, Pivot
from .filters import FilterCompiler
from .object_inputs import input_plan
from .planner import pivot_operation


class ClickHouseCompiler:
    """Owns dialect details. No client SQL or expression strings enter this compiler."""

    def __init__(self, database):
        self.database = database
        self.input_checks = []

    def filter_sql(self, definition, available):
        return FilterCompiler.compile(definition, available)

    def table(self, name):
        return exp.Table(
            this=exp.to_identifier(name, quoted=True),
            db=exp.to_identifier(self.database, quoted=True),
        ).sql(dialect="clickhouse")

    def object_input(self, owner, relation):
        plan = input_plan(owner, relation["columns"], relation["physicalName"])
        if plan is None:
            return self.table(relation["physicalName"]), relation["columns"]
        compiled = ClickHouseCompiler(self.database).compile(plan)
        for check in compiled.checks:
            if check not in self.input_checks:
                self.input_checks.append(check)
        return "(" + compiled.sql + ")", list(compiled.schema.columns)

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
        elif pivot_operation(plan.root.stage) in ("expand_rows", "group_values"):
            stage = plan.root.stage
            alias = q("__semantic_pivot_input")

            def column(column_id):
                return f"{alias}.{q(names[column_id])}"

            selected = [column(k) for k in stage["groupByColumnIds"]]
            types = {c["columnId"]: c["physicalType"] for c in plan.schema.columns}
            for pair in stage["pairs"]:
                attr, value = (
                    column(pair["attributeColumnId"]),
                    column(pair["valueColumnId"]),
                )
                for output in pair["outputs"]:
                    condition = (
                        f"isNull({attr})"
                        if output["attributeValue"] is None
                        else f"{attr} = {self.literal(output['attributeValue'])}"
                    )
                    typ = types[output["columnId"]]
                    if typ.startswith("LowCardinality("):
                        typ = typ[15:-1]
                    if not typ.startswith("Nullable("):
                        typ = f"Nullable({typ})"
                    selected.append(
                        f"if({condition}, {value}, CAST(NULL AS {typ})) AS {q(output['name'])}"
                    )
            # Project pair outputs once per input context. Legacy expansion
            # preserves occurrences; explicit grouping collapses identical tuples.
            sql = f"SELECT {', '.join(selected)} FROM {table} AS {alias}"
            if pivot_operation(stage) == "group_values":
                # Group complete records by retained columns and each pair's
                # discriminator/value tuple. Identical input records collapse.
                grouped = list(
                    dict.fromkeys(
                        [
                            *stage["groupByColumnIds"],
                            *[
                                cid
                                for pair in stage["pairs"]
                                for cid in (
                                    pair["attributeColumnId"],
                                    pair["valueColumnId"],
                                )
                            ],
                        ]
                    )
                )
                sql += " GROUP BY " + ", ".join(column(cid) for cid in grouped)
        else:
            stage = plan.root.stage
            group_ids = stage.get("recordKeyColumnIds") or stage["groupByColumnIds"]
            keys = [q(names[k]) for k in group_ids]
            selected, aggregates, guards = [], [], []
            retained_guards = []
            carried = [cid for cid in stage["groupByColumnIds"] if cid not in group_ids]
            for index, cid in enumerate(stage["groupByColumnIds"]):
                column = q(names[cid])
                if cid in group_ids:
                    selected.append(column)
                else:
                    alias_name = "__semantic_retained_" + str(index)
                    while alias_name in names.values() or alias_name in [
                        c["name"] for c in plan.schema.columns
                    ]:
                        alias_name += "_"
                    alias = q(alias_name)
                    aggregates.append(f"groupUniqArray(2)(tuple({column})) AS {alias}")
                    selected.append(f"tupleElement({alias}[1],1) AS {column}")
                    retained_guards.append(f"length({alias})>1")
            if carried:
                fields = ", ".join([*keys, *(q(names[cid]) for cid in carried)])
                checks.append(
                    Check(
                        "conflicting_retained_values",
                        f"SELECT {', '.join(keys)},count() FROM (SELECT DISTINCT {fields} FROM {table}) GROUP BY {', '.join(keys)} HAVING count()>1 LIMIT 20",
                    )
                )
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
            if retained_guards:
                sql += f" AND throwIf({' OR '.join(retained_guards)}, 'SEMANTIC_CONFLICTING_RETAINED') = 0"
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
        return ExecutionPlan(
            ast,
            tuple(checks),
            plan.schema,
            compiler_version="managed-v3-explicit-grouping-sqlglot-27.28.1"
            if isinstance(plan.root, Pivot)
            and plan.root.stage.get("recordKeyColumnIds") is not None
            else "managed-v2-expand-rows-sqlglot-27.28.1"
            if isinstance(plan.root, Pivot)
            and pivot_operation(plan.root.stage) == "expand_rows"
            else "managed-v1-sqlglot-27.28.1",
        )
