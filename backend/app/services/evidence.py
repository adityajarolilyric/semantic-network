from .records import RevisionRecordService


class EvidenceService(RevisionRecordService):
    collection = "semantic_evidence"
    allow_published = True
