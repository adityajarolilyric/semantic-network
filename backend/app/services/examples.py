from ..repositories.metadata.records import RecordCategory
from .records import RevisionRecordService


class ExampleService(RevisionRecordService):
    record_category = RecordCategory.EXAMPLE
