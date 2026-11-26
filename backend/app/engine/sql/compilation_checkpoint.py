"""Rollback a speculative measure branch without breaking context aliases."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CompilationCheckpoint:
    collections: tuple[tuple[dict | list | set, dict | list | set], ...]

    @classmethod
    def capture(cls, compiler):
        # Keep the original containers: CompilationContext owns CTEs/input checks
        # and other operators retain references to them. Definitions are read-only.
        names = (
            "ctes",
            "cte_dependencies",
            "cache",
            "transaction_relations",
            "assertions",
            "_lookup_checks",
            "nodes",
            "coverage_nodes",
            "mixed_nodes",
            "object_inputs",
            "shared_relations",
            "native_groups",
        )
        extras = (
            compiler.checkpoint_collections()
            if hasattr(compiler, "checkpoint_collections")
            else []
        )
        return cls(
            tuple((collection, collection.copy()) for collection in extras)
            + tuple(
                (getattr(compiler, name), getattr(compiler, name).copy())
                for name in names
            )
        )

    def restore(self):
        for collection, saved in self.collections:
            collection.clear()
            if isinstance(collection, list):
                collection.extend(saved)
            else:
                collection.update(saved)
