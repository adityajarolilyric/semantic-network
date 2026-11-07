"""Detached operation-local metadata and shared indexes, never a cross-request cache."""

from copy import deepcopy
from types import MappingProxyType


class ResolvedGraph(tuple):
    """Resolved grain contexts, indexed once for read-only engine consumers.

    The sequence and indexes cannot be changed. Nested documents are detached from
    catalog inputs and read-only by contract; authoring changes must create a new
    document/list and resolve it again. Mutable execution state belongs to compilers.
    """

    def __new__(cls, documents):
        instance = super().__new__(cls, documents)
        instance.by_id = MappingProxyType({doc["objectId"]: doc for doc in instance})
        kinds = {doc["kind"] for doc in instance}
        instance.by_kind = MappingProxyType(
            {
                kind: MappingProxyType(
                    {doc["objectId"]: doc for doc in instance if doc["kind"] == kind}
                )
                for kind in kinds
            }
        )
        return instance

    def __deepcopy__(self, memo):
        # A caller asking to edit a snapshot receives ordinary detached documents,
        # so the resolver cannot mistake edited data for an already-resolved graph.
        return deepcopy(list(self), memo)


class EffectiveGraph(ResolvedGraph):
    """Also includes managed output schemas and expanded member catalogs."""
