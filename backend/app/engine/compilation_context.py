"""Compilation state for input transformations, independent of measures."""

from ..core.errors import MetadataError
from ..domain.measure_policies import normalize_measure
from ..warehouse.repository import quote_identifier as q
from .planning_compiler import PlanningLevelCompiler


class CompilationContext(PlanningLevelCompiler):
    def __init__(self, database, objects, managed, *, resolution_max_rows=1000000):
        super().__init__(database)
        self.objects = {
            document["objectId"]: normalize_measure(document) for document in objects
        }
        self.managed = managed
        self.resolution_max_rows = resolution_max_rows
        self.object_inputs = {}
        self.ctes = {}
        self.assertions = self.input_checks
        self.max_nodes = 1024

    def object_input(self, owner, relation):
        from .contracts import digest

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
        return (
            "WITH "
            + ", ".join(q(k) + " AS (" + v + ")" for k, v in self.ctes.items())
            + " "
            + sql
            if self.ctes
            else sql
        )
