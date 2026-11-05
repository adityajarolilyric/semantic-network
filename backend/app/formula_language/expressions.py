"""Structural helpers used by validation, dependency planning and compilation."""


def walk(node):
    if not node:
        return
    yield node
    if node["type"] == "binary":
        yield from walk(node["left"])
        yield from walk(node["right"])
    elif node["type"] in {"call", "count"}:
        for argument in node["arguments"]:
            yield from walk(argument)


def contains_counts(node):
    return any(item["type"] == "count" for item in walk(node))


def count_key(node):
    from ..engine.contracts import digest

    return "@count:" + digest(node)[:24]
