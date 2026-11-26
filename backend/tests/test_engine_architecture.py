"""Keep pure planning separate from I/O and prevent module import cycles."""

import ast
from graphlib import TopologicalSorter
from pathlib import Path

from backend.app.engine.measures.compiler import MeasureCompiler
from .test_incremental import model

ENGINE = Path(__file__).resolve().parents[1] / "app" / "engine"
PURE_PACKAGES = {
    "graph",
    "sql",
    "transformations",
    "populations",
    "measures",
    "decision",
}


def module_name(path):
    relative = path.relative_to(ENGINE).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["backend", "app", "engine", *parts])


def imported_module(path, node):
    if not node.level:
        return node.module or ""
    current = module_name(path).split(".")
    if path.name != "__init__.py":
        current.pop()
    return ".".join(
        [
            *current[: len(current) - node.level + 1],
            *([node.module] if node.module else []),
        ]
    )


def runtime_nodes(nodes):
    for node in nodes:
        if isinstance(node, ast.If) and ast.unparse(node.test) in {
            "TYPE_CHECKING",
            "typing.TYPE_CHECKING",
        }:
            yield from runtime_nodes(node.orelse)
        else:
            yield node
            for _, value in ast.iter_fields(node):
                if isinstance(value, list):
                    yield from runtime_nodes(
                        [child for child in value if isinstance(child, ast.AST)]
                    )
                elif isinstance(value, ast.AST):
                    yield from runtime_nodes([value])


def test_planning_packages_do_not_access_persistence_or_clients():
    violations = []
    for path in ENGINE.rglob("*.py"):
        if path.relative_to(ENGINE).parts[0] not in PURE_PACKAGES:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                target = imported_module(path, node)
                if target.startswith(
                    (
                        "backend.app.repositories",
                        "backend.app.db",
                        "backend.app.services",
                    )
                ):
                    violations.append(
                        (str(path.relative_to(ENGINE)), node.lineno, target)
                    )
            if isinstance(node, ast.AsyncFunctionDef):
                violations.append(
                    (
                        str(path.relative_to(ENGINE)),
                        node.lineno,
                        "async I/O in a planning package",
                    )
                )
    assert not violations, violations


def test_engine_runtime_imports_are_acyclic():
    paths = {module_name(path): path for path in ENGINE.rglob("*.py")}
    dependencies = {}
    for name, path in paths.items():
        imports = set()
        for node in runtime_nodes(ast.parse(path.read_text()).body):
            if isinstance(node, ast.ImportFrom):
                target = imported_module(path, node)
                if target in paths:
                    imports.add(target)
        dependencies[name] = imports
    tuple(TopologicalSorter(dependencies).static_order())


def test_specialised_compilers_have_operation_local_collaborators():
    objects, populations, managed = model()
    first = MeasureCompiler("fixture", objects, populations, managed, "base")
    second = MeasureCompiler("fixture", objects, populations, managed, "base")
    first.cte("SELECT 1")
    assert not second.ctes
    assert first.context is not second.context
    for name in (
        "native_compiler",
        "alignment_compiler",
        "formula_compiler",
        "value_compiler",
        "output_compiler",
        "propagation_compiler",
    ):
        assert getattr(first, name).compiler is first
        assert getattr(second, name).compiler is second
        assert getattr(first, name) is not getattr(second, name)
