"""Evidence and examples share record mechanics, not physical names in services."""

from enum import StrEnum

from .collection import CollectionRepository
from .criteria import RecordLookup


class RecordCategory(StrEnum):
    EVIDENCE = "evidence"
    EXAMPLE = "example"


class RecordRepository(CollectionRepository):
    default_order = "recordId"
    criteria_type = RecordLookup

    def __init__(self, documents, category):
        super().__init__(documents)
        self.collection = {
            RecordCategory.EVIDENCE: "semantic_evidence",
            RecordCategory.EXAMPLE: "semantic_examples",
        }[category]
