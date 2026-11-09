"""Memoisation belongs to one metadata capture transaction, never a service singleton."""

from dataclasses import dataclass, field

from .managed import ManagedSourceSnapshot


@dataclass
class PopulationCaptureSession:
    objects_by_id: dict[str, dict]
    populations: dict[tuple[str, bool], dict] = field(default_factory=dict)
    sources: dict[str, ManagedSourceSnapshot] = field(default_factory=dict)
