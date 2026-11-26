"""Pure effective-schema expansion; callers supply captured managed metadata."""

from copy import deepcopy

from ...domain.dimensions import expand_member_catalogs
from .grain_contexts import resolve_owned_grain_contexts
from .resolved_graph import EffectiveGraph


def apply_effective_schema(objects, managed):
    by_id = {d["sourceId"]: d for d in managed}
    result = deepcopy(expand_member_catalogs(objects))
    for obj in result:
        if obj["kind"] == "source" and obj["objectId"] in by_id:
            definition = by_id[obj["objectId"]]["definition"]
            obj["definition"]["columns"] = [
                {k: v for k, v in c.items() if k != "lineage"}
                for c in definition["columns"]
            ]
            obj["definition"]["keyColumns"] = definition["primaryKeyColumnIds"]
    return EffectiveGraph(resolve_owned_grain_contexts(result))
