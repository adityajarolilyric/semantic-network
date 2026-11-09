from dataclasses import dataclass, field


@dataclass
class ViewPlan:
    level_id: str
    exact: bool
    fields: list
    leaves: set
    explanation: str
    native_only: bool = False
    count_source_levels: tuple = ()
    measure_levels: dict = field(default_factory=dict)
