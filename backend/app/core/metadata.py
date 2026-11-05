"""Clock and revision identity helpers with no service or engine dependencies."""

from datetime import UTC, datetime


def utc_now():
    return datetime.now(UTC)


def revision_scope(model_id, revision_id):
    return {"modelId": model_id, "revisionId": revision_id}
