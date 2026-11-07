"""Keep module dependencies explicit and prevent lazy imports hiding runtime cycles."""

import ast
from pathlib import Path

APP = Path(__file__).parents[1] / "app"


def modules():
    result = {}
    for path in APP.rglob("*.py"):
        parts = list(path.relative_to(APP).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        result[".".join(["app", *parts])] = (path, ast.parse(path.read_text()))
    return result


def test_runtime_imports_are_at_module_scope():
    violations = []
    for path, tree in modules().values():
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                violations.extend(
                    f"{path.relative_to(APP)}:{node.lineno}"
                    for node in ast.walk(function)
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                )
    assert not violations, violations


def test_runtime_module_dependencies_are_acyclic():
    source_modules = modules()
    graph = {name: set() for name in source_modules}

    def imports(tree):
        if (
            isinstance(tree, ast.If)
            and isinstance(tree.test, ast.Name)
            and tree.test.id == "TYPE_CHECKING"
        ):
            return
        if isinstance(tree, (ast.Import, ast.ImportFrom)):
            yield tree
        for child in ast.iter_child_nodes(tree):
            yield from imports(child)

    for name, (path, tree) in source_modules.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in imports(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                base = (
                    ".".join(
                        package.split(".")[: len(package.split(".")) - node.level + 1]
                    )
                    if node.level
                    else ""
                )
                target = ".".join(filter(None, [base, node.module]))
                targets = [target, *(target + "." + alias.name for alias in node.names)]
            graph[name].update(
                target
                for target in targets
                if target in source_modules and target != name
            )

    completed = set()
    visiting = []

    def visit(module):
        assert module not in visiting, "Circular import: " + " -> ".join(
            [*visiting, module]
        )
        if module in completed:
            return
        visiting.append(module)
        for dependency in sorted(graph[module]):
            visit(dependency)
        visiting.pop()
        completed.add(module)

    for module in graph:
        visit(module)
