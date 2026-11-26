"""Canonical metadata ownership, independent of any physical population table."""

from copy import deepcopy
from hashlib import sha256


def canonical_measure(document):
    if document.get("kind") != "measure":
        return document
    result = deepcopy(document)
    definition = result["definition"]
    legacy = definition.pop("nativeDimensionId", None)
    legacy = definition.pop("nativeGrainId", None) or legacy
    if (
        definition["measureType"] == "calculated"
        and not definition.get("outputGrain")
        and legacy
    ):
        definition["outputGrain"] = {"kind": "dimension", "objectId": legacy}
    return result


def transaction_grain_id(transaction_id):
    # Owner identity is sufficient; this is a logical context, never a semantic object.
    return "tg_" + sha256(transaction_id.encode()).hexdigest()[:28]


def resolve_grain(reference, objects):
    if not reference:
        return None
    if reference["kind"] == "dimension":
        return reference["objectId"]
    transaction = next(
        (
            o
            for o in objects
            if o["objectId"] == reference["objectId"] and o["kind"] == "transaction"
        ),
        None,
    )
    if not transaction:
        return transaction_grain_id(reference["objectId"])
    return transaction["definition"].get("dimensionId") or transaction_grain_id(
        transaction["objectId"]
    )


def transaction_column_reference(transaction_id, column_id, objects):
    transaction = next(
        (
            o
            for o in objects
            if o["objectId"] == transaction_id and o["kind"] == "transaction"
        ),
        None,
    )
    if transaction is None:
        return None, None
    definition = transaction["definition"]
    grain_id = definition.get("dimensionId") or transaction_grain_id(transaction_id)
    if definition.get("dimensionId"):
        mapped = next(
            (
                m["grainColumnId"]
                for m in definition.get("keyMappings", [])
                if m["sourceColumnId"] == column_id
            ),
            None,
        )
        return grain_id, mapped
    return grain_id, column_id if column_id in definition.get(
        "grainColumnIds", []
    ) else None
