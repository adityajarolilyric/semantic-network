from dataclasses import dataclass


@dataclass
class ViewPlan:
    level_id: str
    exact: bool
    fields: list
    leaves: set
    explanation: str
    native_only: bool = False
    count_source_levels: tuple = ()
