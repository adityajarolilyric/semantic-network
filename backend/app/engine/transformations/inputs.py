"""Object-owned input pivots share operators with managed tables, never mutate them."""

from ...schemas.managed_tables import ManagedDefinition
from .pivot.planner import ManagedTablePlanner


def input_plan(owner, columns, physical_name="input"):
    pivot = owner["definition"].get("pivot")
    if not pivot or not pivot.get("enabled", True):
        return None
    source = {
        "objectId": owner["objectId"],
        "definition": {"tableName": physical_name, "columns": columns},
    }
    return ManagedTablePlanner.plan(source, ManagedDefinition(pipeline=[pivot]))


def input_columns(owner, columns):
    plan = input_plan(owner, columns)
    return list(plan.schema.columns) if plan else list(columns)
