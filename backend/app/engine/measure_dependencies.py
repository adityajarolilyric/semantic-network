"""Formula dependency DAG derived from stable AST references, never persisted twice."""

from collections import deque
from graphlib import CycleError, TopologicalSorter

from ..core.errors import MetadataError
from ..formula_language.expressions import measure_references


class MeasureDependencies:
    def __init__(self, objects):
        self.measures = {
            obj["objectId"]: obj for obj in objects if obj["kind"] == "measure"
        }
        self.direct = {
            mid: measure_references(
                (obj["definition"].get("formula") or {}).get("expression")
            )
            for mid, obj in self.measures.items()
        }
        self.orders = {}
        self.reverse = {mid: [] for mid in self.measures}
        for parent, children in self.direct.items():
            for child in children:
                self.reverse.setdefault(child, []).append(parent)
        self.paths_to = {}

    def name(self, measure_id):
        return self.measures.get(measure_id, {}).get("name", "Missing measure")

    def path(self, start, end):
        if end not in self.paths_to:
            paths = {end: (end,)}
            pending = deque([end])
            while pending:
                node = pending.popleft()
                for parent in self.reverse.get(node, ()):
                    if parent not in paths:
                        paths[parent] = (parent, *paths[node])
                        pending.append(parent)
            self.paths_to[end] = paths
        return self.paths_to[end].get(start, ())

    def ordered(self, root):
        """Validate just the requested closure; return dependencies before consumers."""
        if root in self.orders:
            return self.orders[root]
        closure, pending = {}, [root]
        while pending:
            node = pending.pop()
            if node in closure:
                continue
            if node not in self.measures:
                raise MetadataError(
                    "measure_path_unavailable",
                    f'"{self.name(root)}" references a measure that no longer exists.',
                )
            closure[node] = self.direct[node]
            pending.extend(self.direct[node])
        sorter = TopologicalSorter(closure)
        try:
            sorter.prepare()
        except CycleError as error:
            cycle = list(reversed(error.args[1]))
            raise MetadataError(
                "measure_dependency_cycle",
                "Measure dependency cycle: "
                + " → ".join(self.name(mid) for mid in cycle),
            ) from error
        ordered, depths = [], {}
        while sorter.is_active():
            ready = sorted(sorter.get_ready())
            for mid in ready:
                depths[mid] = 1 + max(
                    (depths[child] for child in closure[mid]), default=0
                )
                if depths[mid] > 128:
                    raise MetadataError(
                        "measure_dependency_depth",
                        "Measure dependency nesting exceeds 128 levels.",
                    )
                ordered.append(mid)
            sorter.done(*ready)
        self.orders[root] = tuple(ordered)
        return self.orders[root]
