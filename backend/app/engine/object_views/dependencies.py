"""Object-view dependencies exclude measures and mutable source values."""


def references(document, objects):
    by_id = {o["objectId"]: o for o in objects}
    owners = {
        o.get("memberCatalogId"): o["objectId"]
        for o in objects
        if o["kind"] == "dimension" and o.get("memberCatalogId")
    }
    definition = document["definition"]
    ids = set()

    def visit(value, key=None):
        if isinstance(value, dict):
            for k, child in value.items():
                visit(child, k)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif key in {"sourceDimensionId", "dimensionId", "memberCatalogId"} and value:
            target = owners.get(value, value)
            if target in by_id and target != document["objectId"]:
                ids.add(target)

    visit(definition)
    return ids


def input_tokens(document, objects, source_bindings):
    by_id = {o["objectId"]: o for o in objects}
    pending, dependencies, sources = [document], {}, set()
    seen = set()
    while pending:
        current = pending.pop()
        if current["objectId"] in seen:
            continue
        seen.add(current["objectId"])

        def visit(value, key=None):
            if isinstance(value, dict):
                for k, child in value.items():
                    visit(child, k)
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif key == "sourceId" and value:
                sources.add(value)

        visit(current)
        for did in references(current, objects):
            dependencies[did] = by_id[did]["version"]
            pending.append(by_id[did])
    return {
        "dependencies": dependencies,
        "sourceInputs": {
            sid: source_bindings[sid]["artifactId"]
            for sid in sources
            if sid in source_bindings
        },
    }


def is_current(artifact, document, objects, source_bindings):
    return all(
        artifact.get(key, {}) == value
        for key, value in input_tokens(document, objects, source_bindings).items()
    )
