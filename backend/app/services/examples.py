from .records import RevisionRecordService


class ExampleService(RevisionRecordService):
    collection = "semantic_examples"
