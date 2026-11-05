from ..repositories.metadata.records import RecordCategory
from .records import RevisionRecordService


class EvidenceService(RevisionRecordService):
    record_category = RecordCategory.EVIDENCE
    allow_published = True
