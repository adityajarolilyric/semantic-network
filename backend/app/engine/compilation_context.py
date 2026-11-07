"""Compilation state for input transformations, independent of measures."""

from sqlglot import exp, parse_one

from ..core.errors import MetadataError
from ..warehouse.sql import quote_identifier as q
from .compiler import ClickHouseCompiler
from .contracts import digest
from .grain_contexts import resolve_grain_contexts


class CompilationContext(ClickHouseCompiler):
    def __init__(self, database, objects, managed, *, resolution_max_rows=1000000):
        super().__init__(database)
        self.graph = resolve_grain_contexts(objects)
        self.objects = dict(self.graph.by_id)
        self.managed = managed
        self.resolution_max_rows = resolution_max_rows
        self.object_inputs = {}
        self.ctes = {}
        self.cte_dependencies = {}
        self.assertions = self.input_checks
        self.max_nodes = 1024

    def object_input(self, owner, relation):
        pivot = owner["definition"].get("pivot")
        if not pivot or not pivot.get("enabled", True):
            return super().object_input(owner, relation)
        identity = digest(
            [
                owner["objectId"],
                pivot,
                relation.get("artifactId", relation["physicalName"]),
            ]
        )
        if identity not in self.object_inputs:
            table, columns = super().object_input(owner, relation)
            node = self.cte("SELECT * FROM " + table)
            self.object_inputs[identity] = (q(node), columns)
        return self.object_inputs[identity]

    def cte(self, sql):
        if len(self.ctes) >= self.max_nodes:
            raise MetadataError(
                "measure_graph_limit",
                "Measure execution exceeds the configured dependency-node limit.",
            )
        name = "measure_node_" + str(len(self.ctes))
        self.ctes[name] = sql
        return name

    def with_sql(self, sql):
        def dependencies(statement):
            return {
                table.name
                for table in parse_one(statement, read="clickhouse").find_all(exp.Table)
                if not table.db and table.name in self.ctes
            }

        pending = list(dependencies(sql)) if self.ctes else []
        required = set()
        while pending:
            name = pending.pop()
            if name in required:
                continue
            required.add(name)
            if name not in self.cte_dependencies:
                self.cte_dependencies[name] = dependencies(self.ctes[name])
            pending.extend(self.cte_dependencies[name])
        return (
            "WITH "
            + ", ".join(
                q(k) + " AS (" + v + ")" for k, v in self.ctes.items() if k in required
            )
            + " "
            + sql
            if required
            else sql
        )
