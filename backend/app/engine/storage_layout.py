"""Deterministic storage ordering from explicit time metadata and initial data."""

from .relationships import LevelRelationshipAnalyzer


def time_key_ids(dimension, objects):
    keys = dimension["definition"]["grain"]["keyIds"]
    if dimension.get("isTimeDim"):
        return set(keys)
    outputs = LevelRelationshipAnalyzer(objects).outputs(dimension["objectId"])
    return {key for key in keys if outputs[key].is_time}


def sorting_key(key_ids, cardinalities, time_keys):
    positions = {key: index for index, key in enumerate(key_ids)}
    return sorted(
        key_ids,
        key=lambda key: (
            key not in time_keys,
            cardinalities.get(key, 0),
            positions[key],
        ),
    )
