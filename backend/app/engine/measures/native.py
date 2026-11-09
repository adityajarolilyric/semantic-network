from dataclasses import replace

from sqlglot import parse_one

from ...core.errors import MetadataError
from ...warehouse.sql import quote_identifier as q
from ..sql.contracts import Check
from ..transformations.filters import FilterCompiler
from ..transformations.multigrain.compiler import TransactionResolutionCompiler
from ..transformations.multigrain.contracts import ResolvedTransaction
from ..transformations.pipeline import InputPipelineCompiler, PreviewTarget
from .contracts import COMPLETE, VALUE
from .conversion import MeasureConversionCompiler
from .native_aggregation import direct_transaction_group
from .range_aggregation import RangeMeasureAggregator


class NativeMeasureCompiler:
    """Resolve Transaction inputs and aggregate native measure values."""

    def __init__(self, compiler):
        self.compiler = compiler

    def resolve_transaction(self, transaction_id):
        if transaction_id not in self.compiler.transaction_relations:
            tx = self.compiler.objects[transaction_id]
            if tx["definition"].get("mappingMode") != "resolve_multi_grain":
                target = (
                    PreviewTarget.CALENDAR_INPUT
                    if tx["definition"].get("calendarResolution")
                    else PreviewTarget.PIVOT_INPUT
                )
                resolved_input = InputPipelineCompiler(self.compiler).compile(
                    tx, target
                )
                node, input_cols = (resolved_input.node, resolved_input.columns)
                cols = [{**col, "storageName": col["columnId"]} for col in input_cols]
                result = ResolvedTransaction(
                    node, cols, tx["definition"]["keyMappings"], node, [node], node
                )
                self.compiler.transaction_relations[transaction_id] = (
                    self.compiler.filter_transaction(tx, result)
                )
                return self.compiler.transaction_relations[transaction_id]
            if transaction_id not in self.compiler.resolution_profiles:
                raise MetadataError(
                    "resolution_not_prepared",
                    "Prepare the transaction matching profiles before compiling measures.",
                )
            result = TransactionResolutionCompiler(
                self.compiler,
                tx,
                self.compiler.resolution_profiles[transaction_id],
                self.compiler.resolution_max_rows,
            ).compile()
            self.compiler.transaction_relations[transaction_id] = (
                self.compiler.filter_transaction(tx, result)
            )
        return self.compiler.transaction_relations[transaction_id]

    def filter_transaction(self, transaction, resolved):
        filters = transaction["definition"].get("filters")
        if not filters:
            return resolved
        sql = FilterCompiler.apply(
            "SELECT * FROM " + q(resolved.node), filters, resolved.columns
        )
        return replace(
            resolved, node=self.compiler.cte(sql), unfiltered_node=resolved.node
        )

    def compile_native_measure(self, measure):
        d = measure["definition"]
        level = d["nativeGrainId"]
        tx = self.compiler.objects[d["transactionId"]]["definition"]
        if tx.get("mappingMode") == "resolve_multi_grain" or (
            tx.get("calendarResolution") or tx.get("pivot") or tx.get("filters")
        ):
            resolved = self.compiler.resolve_transaction(d["transactionId"])
            table = q(resolved.node)
            columns = {
                c["columnId"]: {**c, "name": c["storageName"]} for c in resolved.columns
            }
            mappings = resolved.mappings
        else:
            source = self.compiler.managed[tx["sourceId"]]
            table = self.compiler.table(source["physicalName"])
            columns = self.compiler.source_columns(tx["sourceId"])
            mappings = tx["keyMappings"]
        typed = d["resultType"]["type"] in {"string", "date", "boolean"} or d.get(
            "isConversionFactor", False
        )
        grouped = None
        value_column, mixed_column = (VALUE, "__mixed")
        if tx.get("mappingMode") == "resolve_multi_grain" or (
            tx.get("calendarResolution") or tx.get("pivot") or tx.get("filters")
        ):
            grouped = RangeMeasureAggregator(
                self.compiler,
                self.compiler.objects[d["transactionId"]],
                resolved,
                measure,
            ).compile()
        if grouped is None:
            grouped, value_column, mixed_column = direct_transaction_group(
                self.compiler, measure, table, columns, mappings
            )
        # Aggregate fact rows before the LEFT JOIN; reject assignments outside the population.
        self.compiler.assertions.append(
            Check(
                "unmatched_transaction_grain",
                f"SELECT a.* FROM {q(grouped)} a LEFT JOIN (SELECT *,toUInt8(1) AS __matched FROM ({self.compiler.population(level)})) p ON {self.compiler.join_keys('a', 'p', level)} WHERE coalesce(p.__matched,0)=0 LIMIT 20",
            )
        )
        absent = "0" if d["missingTransactionValue"] == "zero" else "NULL"
        result = self.compiler.cte(
            f"SELECT {', '.join(('p.' + q(k) + ' AS ' + q(k) for k in self.compiler.keys(level)))}, if(coalesce(a.__present,0)=0,{absent},a.{q(value_column)}) AS {q(VALUE)}, {(f'if(coalesce(a.{q(mixed_column)},0),toUInt8(2),toUInt8(1))' if typed else 'toUInt8(1)')} AS {q(COMPLETE)} FROM ({self.compiler.population(level)}) p LEFT JOIN {q(grouped)} a ON {self.compiler.join_keys('p', 'a', level)}"
        )
        self.compiler.shared_relations[result] = parse_one(
            self.compiler.ctes[result], read="clickhouse"
        )
        if typed or d.get("isConversionFactor"):
            self.compiler.mixed_nodes.add(result)
        if d.get("isConversionFactor"):
            self.compiler.assertions.append(
                Check(
                    "invalid_conversion_factor",
                    f"SELECT * FROM {q(result)} WHERE {q(COMPLETE)}=2 OR (isNotNull({q(VALUE)}) AND (NOT isFinite({q(VALUE)}) OR {q(VALUE)}<=0)) LIMIT 20",
                )
            )
        return MeasureConversionCompiler(self.compiler).apply(measure, result)
