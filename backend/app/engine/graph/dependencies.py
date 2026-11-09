"""Deterministic dependency ordering using Python's standard DAG implementation."""

from graphlib import CycleError, TopologicalSorter

from ...core.errors import Conflict


def ordered_dimensions(objects):
    dimensions = {obj["objectId"]: obj for obj in objects if obj["kind"] == "dimension"}
    predecessors = {}
    for object_id, dimension in dimensions.items():
        population = dimension["definition"]["population"]
        parents = (
            {population["sourceDimensionId"]}
            if population["operation"] == "distinct_projection"
            else set()
        )
        if not parents <= dimensions.keys():
            raise Conflict(
                "Dimension dependencies contain a cycle or a missing parent."
            )
        predecessors[object_id] = parents
    sorter = TopologicalSorter(predecessors)
    try:
        sorter.prepare()
    except CycleError as error:
        raise Conflict(
            "Dimension dependencies contain a cycle or a missing parent."
        ) from error
    result = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        result.extend(dimensions[object_id] for object_id in ready)
        sorter.done(*ready)
    return result
