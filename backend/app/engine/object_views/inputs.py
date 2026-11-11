"""Capture saved object views alongside source inputs using stable identities."""

from ...repositories.metadata import metadata_repositories
from ...repositories.metadata.criteria import ArtifactLookup, BindingLookup
from .contracts import definition_hash
from .dependencies import is_current, references


class ObjectViewInputs:
    async def load(self, repo, model, revision, objects):
        repositories = metadata_repositories(repo)
        bindings = await repositories.bindings.list(
            BindingLookup(
                model_id=model, revision_id=revision, resource_kind="objectView"
            ),
            limit=10001,
        )
        source_bindings = {
            b["sourceId"]: b
            for b in await repositories.bindings.list(
                BindingLookup(
                    model_id=model, revision_id=revision, resource_kind="managedTable"
                ),
                limit=10001,
            )
        }
        by_id = {o["objectId"]: o for o in objects}
        relations, captured = {}, {}
        for binding in bindings:
            document = by_id.get(binding["resourceId"])
            if not document or binding["definitionHash"] != definition_hash(document):
                continue
            artifact = await repositories.artifacts.require(
                ArtifactLookup(artifact_id=binding["artifactId"]), "Saved object view"
            )
            if artifact["state"] != "ready" or not is_current(
                artifact, document, objects, source_bindings
            ):
                continue
            relations[document["objectId"]] = artifact
            captured["object:" + document["objectId"]] = binding
            if document.get("memberCatalogId") and artifact.get("memberColumns"):
                members = artifact["memberColumns"]
                relations[document["memberCatalogId"]] = {
                    **artifact,
                    "viewRole": "members",
                    "memberSchema": members,
                    "columns": [
                        {**c, "columnId": c["sourceColumnId"], "name": c["storageName"]}
                        for c in members
                    ],
                }
        return relations, captured

    @staticmethod
    def select(document, objects, views, bindings):
        by_id = {o["objectId"]: o for o in objects}
        pending, allowed = [document], set()
        while pending:
            current = pending.pop()
            if current["objectId"] in allowed:
                continue
            allowed.add(current["objectId"])
            for oid in references(current, objects):
                pending.append(by_id[oid])
        allowed.update(
            by_id[oid]["memberCatalogId"]
            for oid in list(allowed)
            if oid in by_id and by_id[oid].get("memberCatalogId")
        )
        return (
            {oid: artifact for oid, artifact in views.items() if oid in allowed},
            {
                key: binding
                for key, binding in bindings.items()
                if binding["resourceId"] in allowed
            },
        )
