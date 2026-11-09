"""Transient SQL key sets for an edit's cell-level dependency closure."""

from contextlib import contextmanager

from ...formula_language.expressions import contains_counts, contains_temporal
from .temporal_impact import TemporalEditImpact
from ...warehouse.sql import quote_identifier as q
from ..measures.contracts import COMPLETE, VALUE


class EditCellImpact:
    def __init__(self, compiler):
        self.compiler = compiler
        self.scopes = {}
        self.mappings = {}
        self.full_nodes = set()
        self.suspended = 0

    @contextmanager
    def previous_values(self):
        self.suspended += 1
        try:
            yield
        finally:
            self.suspended -= 1

    def restrict_query(self, query, scope, level):
        keys = ", ".join(q(key) for key in self.compiler.keys(level))
        return f"SELECT * FROM ({query}) WHERE tuple({keys}) IN (SELECT tuple({keys}) FROM {q(scope)})"

    def map_keys(self, scope, source, target):
        if source == target:
            return scope
        key = scope, source, target
        if key in self.mappings:
            return self.mappings[key]
        c = self.compiler
        relationship = c.relationships.analyze(source, target)["relationship"]
        with self.previous_values():
            if relationship in {"same", "equivalent", "roll_up"}:
                coordinates = c.relationships.mapping(source, target)
                seed = c.cte(f"SELECT *, NULL AS {q(VALUE)} FROM {q(scope)}")
                mapped = c.enrich(
                    seed, source, {"__impact_" + k: v for k, v in coordinates.items()}
                )
                sql = (
                    "SELECT DISTINCT "
                    + ", ".join(
                        q("__impact_" + k) + " AS " + q(k) for k in c.keys(target)
                    )
                    + f" FROM ({mapped})"
                )
            elif relationship == "roll_down":
                coordinates = c.relationships.mapping(target, source)
                seed = c.cte(
                    "SELECT "
                    + ", ".join(q(k) for k in c.keys(target))
                    + f", NULL AS {q(VALUE)} FROM ({c.population(target)})"
                )
                mapped = c.enrich(
                    seed, target, {"__impact_" + k: v for k, v in coordinates.items()}
                )
                sql = (
                    "SELECT DISTINCT "
                    + ", ".join("r." + q(k) for k in c.keys(target))
                    + f" FROM ({mapped}) r INNER JOIN {q(scope)} s ON "
                    + " AND ".join(
                        "r." + q("__impact_" + k) + "=s." + q(k) for k in c.keys(source)
                    )
                )
            else:
                base = c.base_id
                if not base or base in {source, target}:
                    return None
                bridge = self.map_keys(scope, source, base)
                result = self.map_keys(bridge, base, target) if bridge else None
                self.mappings[key] = result
                return result
        result = c.cte(sql)
        self.mappings[key] = result
        return result

    def union_keys(self, scopes, level):
        scopes = list(dict.fromkeys(scope for scope in scopes if scope))
        if not scopes:
            return None
        if len(scopes) == 1:
            return scopes[0]
        keys = ", ".join(q(key) for key in self.compiler.keys(level))
        return self.compiler.cte(
            "SELECT DISTINCT "
            + keys
            + " FROM ("
            + " UNION ALL ".join(f"SELECT {keys} FROM {q(scope)}" for scope in scopes)
            + ")"
        )

    def scope_for(self, mid, level):
        key = mid, level
        if key in self.scopes:
            return self.scopes[key]
        c = self.compiler
        c.dependencies.ordered(mid)
        if mid not in c.affected:
            result = None
        elif mid == c.edit.measureId:
            with self.previous_values():
                c.patch()
            relation = c.relationships.analyze(c.write_id, level)["relationship"]
            result = (
                self.map_keys(c.edit_patch.changed_keys, c.write_id, level)
                if relation in {"same", "equivalent", "roll_up"}
                else None
            )
        else:
            d = c.measures[mid]["definition"]
            native = d["nativeGrainId"]
            if d.get("calculationType") == "AggregateFirst" and not contains_counts(
                d["formula"]["expression"]
            ):
                result = self.union_keys(
                    [
                        self.scope_for(dep, level)
                        for dep in sorted(c.references(d["formula"]["expression"]))
                    ],
                    level,
                )
            elif level == native and contains_temporal(
                (d.get("formula") or {}).get("expression")
            ):
                result = TemporalEditImpact(self).expression_scope(
                    d["formula"]["expression"], native, d["temporal"]
                )
            elif level == native:
                result = self.union_keys(
                    [
                        self.scope_for(dep, native)
                        for dep in sorted(c.references(d["formula"]["expression"]))
                    ],
                    native,
                )
            else:
                native_scope = self.scope_for(mid, native)
                result = None
                if native_scope:
                    if d.get("calculationType") == "AggregateFirst" or d["resultType"][
                        "type"
                    ] in {"string", "date", "boolean"}:
                        if c.relationships.analyze(native, level)["relationship"] in {
                            "same",
                            "equivalent",
                            "roll_up",
                        }:
                            result = self.map_keys(native_scope, native, level)
                    elif level == c.base_id:
                        result = self.map_keys(native_scope, native, level)
                    elif (
                        c.base_id
                        and c.relationships.analyze(c.base_id, level)["relationship"]
                        in {"roll_up", "equivalent"}
                        and c.relationships.analyze(native, c.base_id)["relationship"]
                        != "incompatible"
                    ):
                        base_scope = self.map_keys(native_scope, native, c.base_id)
                        result = (
                            self.map_keys(base_scope, c.base_id, level)
                            if base_scope
                            else None
                        )
                    elif c.relationships.analyze(native, level)["relationship"] in {
                        "same",
                        "equivalent",
                        "roll_up",
                    }:
                        result = self.map_keys(native_scope, native, level)
        self.scopes[key] = result
        return result

    def restrict_node(self, query, scope, level):
        if not scope:
            return query
        c = self.compiler
        result = c.cte(self.restrict_query(f"SELECT * FROM {q(query)}", scope, level))
        if query in c.coverage_nodes:
            c.coverage_nodes[result] = c.coverage_nodes[query]
        if query in c.mixed_nodes:
            c.mixed_nodes.add(result)
        return result

    def overlay(self, mid, level, updated):
        c = self.compiler
        if updated in self.full_nodes or self.suspended:
            return updated
        scope = self.scope_for(mid, level)
        if scope is None:
            return updated
        with self.previous_values():
            old = c.stored(mid, level)
        changed = self.restrict_query(f"SELECT * FROM {q(updated)}", scope, level)
        # A marker, not a NULL-value check: an explicit edit/formula NULL is authoritative.
        flag = (
            f"n.{q(COMPLETE)}"
            if updated in c.coverage_nodes or updated in c.mixed_nodes
            else "toUInt8(1)"
        )
        old_flag = (
            f"o.{q(COMPLETE)}"
            if old in c.coverage_nodes or old in c.mixed_nodes
            else "toUInt8(1)"
        )
        keys = ", ".join("o." + q(k) + " AS " + q(k) for k in c.keys(level))
        measure = c.measures[mid]
        new_value = c.widen("n." + q(VALUE), measure)
        old_value = c.widen("o." + q(VALUE), measure)
        result = c.cte(
            f"SELECT {keys}, if(coalesce(n.__changed,0)=1,{new_value},{old_value}) AS {q(VALUE)}, if(coalesce(n.__changed,0)=1,{flag},{old_flag}) AS {q(COMPLETE)} FROM {q(old)} o LEFT JOIN (SELECT *,toUInt8(1) AS __changed FROM ({changed})) n ON {c.join_keys('o', 'n', level)}"
        )
        if old in c.coverage_nodes or updated in c.coverage_nodes:
            c.coverage_nodes[result] = True
        if old in c.mixed_nodes or updated in c.mixed_nodes:
            c.mixed_nodes.add(result)
        self.full_nodes.add(result)
        return result
