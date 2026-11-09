from ...warehouse.sql import quote_identifier as q
from ...warehouse.sql import quote_literal as lit
from ..graph.relationships import LevelRelationshipAnalyzer
from ..graph.source_storage import SourceStoragePolicy
from ..populations.compiler import DimensionSqlRenderer
from ..sql.compilation_context import CompilationContext
from ..sql.snapshot_reads import snapshot_read
from ..sql.storage import GENERATION, column_sql, token
from .aggregation import aggregate_expression
from .alignment import MeasureAlignmentCompiler
from .dependencies import MeasureDependencies
from .formulas import CalculatedMeasureCompiler
from .native import NativeMeasureCompiler
from .output import MeasureOutputCompiler
from .propagation import MeasurePropagationCompiler
from .values import MeasureValueCompiler


class MeasureCompiler(DimensionSqlRenderer):
    """Compile a measure dependency DAG into ClickHouse CTEs; no client SQL."""

    def __init__(
        self,
        database,
        objects,
        populations,
        managed,
        base_id,
        previous=None,
        resolution_profiles=None,
        resolution_max_rows=1000000,
    ):
        super().__init__(database)
        self.context = CompilationContext(
            database, objects, managed, resolution_max_rows=resolution_max_rows
        )
        self.objects = self.context.objects
        self.dependencies = MeasureDependencies(self.context.graph)
        self.input_checks = self.context.input_checks
        self.measures = self.context.graph.by_kind.get("measure", {})
        self.levels = {
            **self.context.graph.by_kind.get("dimension", {}),
            **self.context.graph.by_kind.get("grain", {}),
        }
        self.relationships = LevelRelationshipAnalyzer(self.context.graph)
        self.populations, self.managed, self.base_id = (populations, managed, base_id)
        self.previous = previous
        self.resolution_profiles = resolution_profiles or {}
        self.resolution_max_rows = resolution_max_rows
        self.source_storage = SourceStoragePolicy(self.objects.values(), managed)
        self.transaction_relations = {}
        self.object_inputs = self.context.object_inputs
        self.ctes = self.context.ctes
        self.cte_dependencies = self.context.cte_dependencies
        self.cache, self.visiting, self.nodes = ({}, set(), [])
        self.temporal_input_depth = 0
        self.coverage_nodes = {}
        self.mixed_nodes = set()
        self.assertions = self.input_checks
        self.max_nodes = 1024
        self._lookup_checks = set()
        self.shared_relations = {}
        self.native_groups = {}
        self.native_workset = None
        self.native_compiler = NativeMeasureCompiler(self)
        self.alignment_compiler = MeasureAlignmentCompiler(self)
        self.formula_compiler = CalculatedMeasureCompiler(self)
        self.value_compiler = MeasureValueCompiler(self)
        self.output_compiler = MeasureOutputCompiler(self)
        self.propagation_compiler = MeasurePropagationCompiler(self)

    def object_input(self, owner, relation):
        self.context.max_nodes = self.max_nodes
        return self.context.object_input(owner, relation)

    def stored_measure(self, mid, level, snapshots):
        artifact = snapshots.get(level, {})
        read = snapshot_read(
            self.table(artifact.get("viewName", artifact["physicalName"]))
            if artifact
            else "__missing_snapshot",
            artifact,
            mid,
            self.keys(level),
        )
        result = self.cte(read.query.sql(dialect="clickhouse"))
        self.shared_relations[result] = read.query
        if read.coverage:
            self.coverage_nodes[result] = True
        if read.mixed:
            self.mixed_nodes.add(result)
        return result

    def keys(self, level):
        return self.levels[level]["definition"]["grain"]["keyIds"]

    def output_population(self, target):
        return self.population(target)

    def formula_count_cache_key(self, key, measure, target):
        return key, measure["definition"]["nativeGrainId"], target

    def count_population(self, measure, source, target):
        return self.population(source)

    def compile_count_argument(self, measure, source, target):
        return self.compile_calculated_measure(measure, source)

    def temporal_population(self, measure, target):
        return self.population(target)

    def formula_population(self, measure, target):
        return self.population(target)

    def population(self, level):
        artifact = self.populations[level]
        if artifact.get("query"):
            return artifact.get("logicalQuery", artifact["query"])
        if artifact.get("storageMode") == "current" and artifact.get("viewName"):
            artifact = {
                **artifact,
                "columns": [
                    {**c, "storageName": c["name"]} for c in artifact["columns"]
                ],
            }
        query = f"SELECT {', '.join((column_sql(c) + ' AS ' + q(c['columnId']) for c in artifact['columns']))} FROM {self.table(artifact.get('viewName', artifact['physicalName']))}"
        return (
            query
            if artifact.get("storageMode") == "current"
            else query + f" WHERE {q(GENERATION)}={lit(token(artifact))}"
        )

    def cte(self, sql):
        self.context.max_nodes = self.max_nodes
        return self.context.cte(sql)

    def with_sql(self, sql):
        return self.context.with_sql(sql)

    def join_keys(self, left, right, level):
        return " AND ".join(
            (f"{left}.{q(k)} = {right}.{q(k)}" for k in self.keys(level))
        )

    def source_columns(self, source):
        return {c["columnId"]: c for c in self.managed[source]["columns"]}

    def widen(self, expression, measure):
        return self.value_compiler.widen(expression, measure)

    def divide(self, left, right, measure):
        return self.value_compiler.divide(left, right, measure)

    def aggregate(self, expression, method, policy, ordering=None, state=None):
        return aggregate_expression(expression, method, policy, ordering, state)

    def resolve_transaction(self, transaction_id):
        return self.native_compiler.resolve_transaction(transaction_id)

    def filter_transaction(self, transaction, resolved):
        return self.native_compiler.filter_transaction(transaction, resolved)

    def compile_native_measure(self, measure):
        return self.native_compiler.compile_native_measure(measure)

    def enrich(self, query, level, coordinates):
        return self.alignment_compiler.enrich(query, level, coordinates)

    def ordering(self, level):
        return self.alignment_compiler.ordering(level)

    def rollup(self, query, measure, source, target):
        return self.alignment_compiler.rollup(query, measure, source, target)

    def align(self, query, level):
        return self.alignment_compiler.align(query, level)

    def disaggregate(self, parent, measure, source, target):
        return self.alignment_compiler.disaggregate(parent, measure, source, target)

    def with_coverage(self, query, coverage, level, fallback=None):
        return self.alignment_compiler.with_coverage(query, coverage, level, fallback)

    def compile_formula(self, node, measure, target, refs):
        return self.formula_compiler.compile_formula(node, measure, target, refs)

    def compile_calculated_measure(self, measure, target):
        return self.formula_compiler.compile_calculated_measure(measure, target)

    def compile_seed_measure(self, measure_id):
        return self.propagation_compiler.compile_seed_measure(measure_id)

    def compile_measure_at_level(self, measure_id, target):
        return self.propagation_compiler.compile_measure_at_level(measure_id, target)

    def rounded_value(self, expression, measure):
        return self.value_compiler.rounded_value(expression, measure)

    def quantize_node(self, query, measure, level):
        return self.value_compiler.quantize_node(query, measure, level)

    def cast_value(self, expression, measure):
        return self.value_compiler.cast_value(expression, measure)

    def cast_node_value(self, alias, node, measure):
        return self.value_compiler.cast_node_value(alias, node, measure)

    def compile_level(self, target, *, include_checks=True):
        return self.output_compiler.compile_level(target, include_checks=include_checks)
