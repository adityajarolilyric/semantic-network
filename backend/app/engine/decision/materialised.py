"""Shared reads of a proven materialised grain; never resolve additional sources."""

from collections import Counter

from sqlglot import exp, parse_one
from sqlglot.optimizer.merge_subqueries import merge_subqueries

from ...formula_language.expressions import walk
from ...formula_language.sql import FormulaSqlCompiler
from ...formula_language.temporal import is_temporal
from ...warehouse.sql import quote_identifier as q
from ..measures.aggregation import AggregationPlan, aggregate_expression
from ..measures.contracts import VALUE
from ..sql.contracts import ExecutionPlan, RelationSchema
from ..sql.snapshot_reads import snapshot_read
from ..sql.storage import column_sql
from .numeric_results import MaterialisedResultProjection
from .relationships import TARGET


class MaterialisedFormulaCompiler(FormulaSqlCompiler):
    """Use columns from the shared aggregate instead of one joined relation per input."""

    def column(self, name, table):
        if name == VALUE:
            return exp.column(table, table="r", quoted=True)
        return super().column(name, table)


class MaterialisedViewCompiler:
    """Batch scalar numeric measures with compatible aggregation stage shapes.

    Counts, attribute lookups, Mixed states and different stage shapes retain the
    general compiler. Both paths are constrained to the planner's single source.
    """

    def __init__(self, host):
        self.host = host

    def compile(self, measure_ids):
        host = self.host
        source = host.view.level_id
        artifact = host.values[source]
        leaves = sorted(host.view.leaves)
        if not leaves or host.view.count_source_levels:
            return None
        if any(
            artifact.get(flag, {}).get(mid)
            for flag in ("coverageColumns", "mixedColumns")
            for mid in leaves
        ):
            return None

        # Restrict this lowering to scalar formulas. Specialized operators keep
        # their existing semantic compiler rather than being approximated here.
        formulas, visited = [], set()

        def collect(mid):
            if mid in visited:
                return True
            visited.add(mid)
            measure = host.measures[mid]
            if measure["definition"]["resultType"]["type"] not in {
                "number",
                "integer",
                "decimal",
            }:
                return False
            if mid in host.view.leaves:
                return True
            expression = measure["definition"]["formula"]["expression"]
            nodes = list(walk(expression))
            if any(
                node["type"] not in {"literal", "measureRef", "binary", "call"}
                for node in nodes
            ):
                return False
            # Temporal calls have their own partition/frame semantics.
            if any(is_temporal(node) for node in nodes):
                return False
            if not all(
                collect(node["measureId"])
                for node in nodes
                if node["type"] == "measureRef"
            ):
                return False
            formulas.append(mid)
            return True

        if not all(collect(mid) for mid in measure_ids):
            return None

        columns = {column["columnId"]: column for column in artifact["columns"]}
        current = artifact.get("storageMode") in {"current", "source"}

        def expression(column_id):
            column = columns.get(column_id)
            if column is None:
                return None
            return column_sql(
                {**column, "storageName": column["name"]} if current else column
            )

        axes = {}
        for field in host.view.fields:
            coordinate = host.relationships.resolve(source, field["coordinate"])
            value = expression(coordinate.column_id) if coordinate else None
            if value is None:
                return None
            axes[field["id"]] = value

        outputs = host.relationships.outputs(source)
        keys = host.keys(source)
        removed_keys = [
            key for key in keys if not host.relationships.resolve(TARGET, outputs[key])
        ]
        removed = [outputs[key] for key in removed_keys]
        remove_time = any(coordinate.is_time for coordinate in removed)
        remove_non_time = any(not coordinate.is_time for coordinate in removed)
        prefix = "__decision_value_"
        reserved = set(keys) | set(axes)
        while any(
            f"{prefix}{index}" in reserved
            for index in range(len(leaves) + len(formulas))
        ):
            prefix = "_" + prefix
        aliases = {mid: f"{prefix}{index}" for index, mid in enumerate(leaves)}
        stages = {}
        order_expression = None
        for mid in leaves:
            policy = host.measures[mid]["definition"]["aggregation"]
            ordered = remove_time and policy["time"] in {"first", "last"}
            if ordered:
                coordinate = host.ordering(source)
                order_expression = (
                    expression(coordinate.column_id) if coordinate else None
                )
                if order_expression is None:
                    return None
            stages[mid] = (
                AggregationPlan(
                    tuple(host.keys(TARGET)),
                    tuple(key for key in removed_keys if outputs[key].is_time),
                    tuple(key for key in removed_keys if not outputs[key].is_time),
                    remove_time,
                    remove_non_time,
                    policy,
                    ordered,
                ).stages()
                if remove_time or remove_non_time
                else ()
            )
            if any(stage.method == "single_value" for stage in stages[mid]):
                return None

        # SUM of SUM, MIN of MIN and MAX of MAX can collapse. AVG, counts,
        # first/last and differing reducers retain the configured axis stages.
        if all(
            len(stages[mid]) == 2
            and stages[mid][0].method == stages[mid][1].method
            and stages[mid][0].method in {"sum", "min", "max"}
            and (
                stages[mid][0].method == "sum"
                or host.measures[mid]["definition"]["aggregation"]["allNullResult"]
                != "zero"
            )
            for mid in leaves
        ):
            stages = {mid: (sequence[-1],) for mid, sequence in stages.items()}
        shapes = {
            tuple(stage.group_keys for stage in sequence)
            for sequence in stages.values()
        }
        if len(shapes) != 1:
            return None

        table = host.table(artifact.get("viewName", artifact["physicalName"]))
        # Reuse snapshot lowering for storage names, nullable values and legacy
        # generation predicates; select all required values in that one relation.
        read = snapshot_read(table, artifact, leaves[0], keys).query
        required_keys = (
            set(stages[leaves[0]][0].group_keys) if stages[leaves[0]] else set()
        )
        selected = {key: expression(key) for key in keys if key in required_keys}
        if any(value is None for value in selected.values()):
            return None
        selected.update(axes)
        for mid in leaves:
            measure_read = snapshot_read(table, artifact, mid, keys).query
            value = next(
                node.this for node in measure_read.expressions if node.alias == VALUE
            )
            selected[aliases[mid]] = value.sql(dialect="clickhouse")
        if order_expression:
            selected["__time_order"] = order_expression
        query = read.select(
            *[
                parse_one(value, read="clickhouse").as_(alias, quoted=True)
                for alias, value in selected.items()
            ],
            append=False,
        )
        # Qualify source columns before projection merging. This also avoids
        # ClickHouse resolving a physical field as a same-named SELECT alias.
        query.args["from"].this.set(
            "alias", exp.TableAlias(this=exp.to_identifier("src"))
        )
        for column in query.find_all(exp.Column):
            if not column.table:
                column.set("table", exp.to_identifier("src"))
        for index, first in enumerate(stages[leaves[0]]):
            selected = ["r." + q(key) + " AS " + q(key) for key in first.group_keys]
            for mid in leaves:
                stage = stages[mid][index]
                value = aggregate_expression(
                    "r." + q(aliases[mid]),
                    stage.method,
                    host.measures[mid]["definition"]["aggregation"],
                    "r." + q("__time_order") if order_expression else None,
                    compact=True,
                )
                selected.append(value + " AS " + q(aliases[mid]))
            if any(stages[mid][index].carry_time_order for mid in leaves):
                selected.append(f"min(r.{q('__time_order')}) AS {q('__time_order')}")
            sql = f"SELECT {', '.join(selected)} FROM ({query.sql(dialect='clickhouse')}) r"
            if first.group_keys:
                sql += " GROUP BY " + ", ".join(
                    "r." + q(key) for key in first.group_keys
                )
            query = parse_one(sql, read="clickhouse")

        # SQLGlot merges projection-only inputs into GROUP BY, while preserving
        # grouped subqueries (including Time/NonTime aggregation boundaries).
        query = merge_subqueries(query)

        for mid in formulas:
            alias = f"{prefix}{len(aliases)}"
            compiler = MaterialisedFormulaCompiler(
                host, host.measures[mid], TARGET, dict(aliases)
            )
            # The scalar compiler normally resolves references to table aliases;
            # here those aliases map directly to shared result columns.
            value = compiler.compile(
                host.measures[mid]["definition"]["formula"]["expression"]
            )
            query = exp.select("r.*", value.as_(alias, quoted=True)).from_(
                query.subquery("r")
            )
            aliases[mid] = alias

        result = MaterialisedResultProjection(host).compile(
            query, measure_ids, aliases, stages
        )
        formula_uses = Counter(mid for mid in measure_ids if mid in formulas)
        formula_uses.update(
            node["measureId"]
            for mid in formulas
            for node in walk(host.measures[mid]["definition"]["formula"]["expression"])
            if node["type"] == "measureRef" and node["measureId"] in formulas
        )
        # Retain shared calculated projections rather than expanding their DAG
        # repeatedly. Single-use formulas and output casts need no extra SELECT.
        output_names = {f"value_{index}" for index in range(len(measure_ids))}
        source_name_collision = not stages[leaves[0]] and bool(
            output_names & {column["name"] for column in artifact["columns"]}
        )
        if not source_name_collision and not any(
            uses > 1 for uses in formula_uses.values()
        ):
            result = merge_subqueries(result)
        return ExecutionPlan(
            result,
            tuple(host.assertions),
            RelationSchema(()),
            "decision-view-materialised-v1",
        )
