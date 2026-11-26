"""Plan metadata rebinding when a Transaction changes its connected Dimension."""

from collections import defaultdict, deque

from ..formula_language.expressions import walk
from .grains import transaction_grain_id
from .typed_values import NUMERIC


def rebind_referenced_measures(transaction, objects):
    """Return changed definitions only; callers own preparation and persistence."""
    return [
        {
            **measure,
            "definition": {
                **measure["definition"],
                "nativeGrainId": (
                    transaction["definition"].get("dimensionId")
                    or transaction_grain_id(transaction["objectId"])
                ),
            },
        }
        for measure in objects
        if measure["kind"] == "measure"
        and measure["definition"]["measureType"] == "referenced"
        and measure["definition"]["transactionId"] == transaction["objectId"]
    ]


def same_source_grain(previous, transaction):
    """Explicit source-key mappings prove identity; matching column names do not."""
    if not previous:
        return False
    old, new = previous["definition"], transaction["definition"]
    if any(
        definition.get("mappingMode", "direct") != "direct" for definition in (old, new)
    ):
        return False
    if any(
        old.get(field) != new.get(field)
        for field in ("sourceId", "pivot", "calendarResolution")
    ):
        return False
    old_keys = [
        mapping["sourceColumnId"] for mapping in old.get("keyMappings", [])
    ] or old.get("grainColumnIds", [])
    new_keys = [
        mapping["sourceColumnId"] for mapping in new.get("keyMappings", [])
    ] or new.get("grainColumnIds", [])
    return bool(old_keys and all(new_keys) and sorted(old_keys) == sorted(new_keys))


def rebind_transaction_measures(previous, transaction, objects):
    """Move owned references and closed scalar calculations at the same input grain.

    Contextual/count expressions, mixed dependency sets and explicitly different
    output Dimensions retain their meaning and are validated without relocation.
    """
    rebound = rebind_referenced_measures(transaction, objects)
    if not rebound or not same_source_grain(previous, transaction):
        return rebound
    old_dimension = previous["definition"].get("dimensionId") or transaction_grain_id(
        previous["objectId"]
    )
    new_dimension = transaction["definition"].get(
        "dimensionId"
    ) or transaction_grain_id(transaction["objectId"])
    candidates, dependants, remaining = {}, defaultdict(set), {}
    for measure in objects:
        definition = measure.get("definition", {})
        if (
            measure["kind"] != "measure"
            or definition["measureType"] != "calculated"
            or definition["nativeGrainId"] != old_dimension
            or definition["resultType"]["type"] not in NUMERIC
        ):
            continue
        nodes = list(walk(definition["formula"]["expression"]))
        if any(
            node["type"] not in {"literal", "measureRef", "binary", "call"}
            for node in nodes
        ):
            continue
        dependencies = {
            node["measureId"] for node in nodes if node["type"] == "measureRef"
        }
        if not dependencies:
            continue
        candidates[measure["objectId"]] = measure
        remaining[measure["objectId"]] = dependencies
        for dependency in dependencies:
            dependants[dependency].add(measure["objectId"])
    ready = deque(measure["objectId"] for measure in rebound)
    while ready:
        dependency = ready.popleft()
        for measure_id in sorted(dependants[dependency]):
            remaining[measure_id].discard(dependency)
            if remaining[measure_id]:
                continue
            measure = candidates[measure_id]
            rebound.append(
                {
                    **measure,
                    "definition": {
                        **measure["definition"],
                        "nativeGrainId": new_dimension,
                        "outputGrain": {
                            "kind": "dimension",
                            "objectId": transaction["definition"]["dimensionId"],
                        }
                        if transaction["definition"].get("dimensionId")
                        else {
                            "kind": "transaction",
                            "objectId": transaction["objectId"],
                        },
                    },
                }
            )
            ready.append(measure_id)
    return rebound
