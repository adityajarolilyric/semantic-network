"""Operation-local grain contexts. Transaction contexts are never catalog Dimensions."""

from copy import deepcopy

from ...core.errors import MetadataError
from ...domain.grains import canonical_measure, resolve_grain, transaction_grain_id
from ...domain.measure_policies import normalize_measure
from ..transformations.dates.calendar import CalendarResolutionPlanner
from .resolved_graph import ResolvedGraph


def resolve_grain_contexts(objects):
    if isinstance(objects, ResolvedGraph):
        return objects
    return resolve_owned_grain_contexts(deepcopy(list(objects)))


def resolve_owned_grain_contexts(objects):
    """Resolve detached documents owned by this operation, without another copy."""
    result = [obj for obj in objects if obj["kind"] != "grain"]
    by_id = {o["objectId"]: o for o in result}
    for transaction in (o for o in result if o["kind"] == "transaction"):
        definition = transaction["definition"]
        dimension_id = definition.get("dimensionId")
        definition["nativeGrainId"] = dimension_id or transaction_grain_id(
            transaction["objectId"]
        )
        if dimension_id:
            definition.setdefault(
                "grainColumnIds",
                [m["sourceColumnId"] for m in definition.get("keyMappings", [])],
            )
            continue
        if "grainColumnIds" not in definition:
            # Input-only Pivot/Calendar previews have no authored Transaction grain.
            continue
        source = by_id.get(definition["sourceId"])
        if not source:
            continue  # Reference validation owns missing source diagnostics.
        columns = {
            c["columnId"]: c
            for c in CalendarResolutionPlanner(
                result, transaction, source["definition"]["columns"]
            ).schema()
        }
        keys = definition.get("grainColumnIds", [])
        definition["keyMappings"] = [
            {"sourceColumnId": key, "grainColumnId": key} for key in keys
        ]
        if definition["nativeGrainId"] in by_id:
            raise MetadataError(
                "grain_identity_conflict",
                "A semantic object uses a reserved Transaction grain identity. Choose a different object ID.",
            )
        context = {
            **{
                k: transaction[k]
                for k in ("modelId", "revisionId", "version", "createdAt", "updatedAt")
                if k in transaction
            },
            "objectId": definition["nativeGrainId"],
            "kind": "grain",
            "name": transaction["name"],
            "owner": {"kind": "transaction", "objectId": transaction["objectId"]},
            "definition": {
                "population": {
                    "operation": "transaction",
                    "transactionId": transaction["objectId"],
                },
                "grain": {
                    "keyIds": keys,
                    "columns": [
                        {
                            "grainColumnId": key,
                            "name": columns.get(key, {}).get("name", key),
                            "populationRef": {"columnId": key},
                        }
                        for key in keys
                    ],
                },
                "populationAttributes": [],
                "memberBindings": [],
                "timeColumnId": definition.get("timeColumnId"),
            },
        }
        by_id[context["objectId"]] = context
    result = list(by_id.values())
    for obj in result:
        if obj["kind"] != "measure":
            continue
        definition = obj["definition"]
        legacy = definition.get("nativeGrainId") or definition.get("nativeDimensionId")
        obj.update(
            canonical_measure(
                normalize_measure(obj) if "resultType" in definition else obj
            )
        )
        definition = obj["definition"]
        if definition["measureType"] == "referenced":
            reference = {
                "kind": "transaction",
                "objectId": definition.get("transactionId"),
            }
            definition["nativeGrainId"] = (
                resolve_grain(reference, result)
                if reference["objectId"] in by_id
                else legacy
            )
        else:
            definition["nativeGrainId"] = resolve_grain(
                definition.get("outputGrain"), result
            )
    return ResolvedGraph(result)


def grain_contexts(objects):
    return {
        o["objectId"]: o
        for o in resolve_grain_contexts(objects)
        if o["kind"] in {"dimension", "grain"}
    }
