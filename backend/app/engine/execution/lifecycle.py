"""Shared revision guard; each handler retains its own activation rules."""

from ...core.errors import Conflict
from ...repositories.metadata import metadata_repositories
from ...repositories.metadata.criteria import RevisionLookup


async def current_build_revision(repository, run):
    revision = await metadata_repositories(repository).revisions.require(
        RevisionLookup(model_id=run["modelId"], revision_id=run["revisionId"]),
        "Revision",
    )
    if revision["version"] != run["revisionVersion"]:
        raise Conflict()
    return revision
