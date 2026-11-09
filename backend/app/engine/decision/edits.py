"Base-or-native writeback against a captured generation, without replay storage."

from ...core.errors import MetadataError
from ...formula_language.expressions import measure_references
from ...warehouse.sql import quote_identifier as q
from ...warehouse.sql import quote_literal as lit
from ..measures.compiler import MeasureCompiler
from ..sql.storage import GENERATION, token
from .allocation import EditAllocationCompiler
from .edit_impact import EditCellImpact
from .temporal_impact import TemporalEditImpact
from .edit_planner import MeasureEditPlanner
from .patch import EditPatchCompiler
from .planner import DecisionViewPlanner
from .relationships import TARGET, ViewRelationships, virtual_level


class MeasureEditCompiler(MeasureCompiler):
    def previous_values_sql(self, artifact):
        query = f"SELECT * FROM {self.table(artifact.get('viewName', artifact['physicalName']))}"
        if artifact.get("storageMode") not in {"current", "source"}:
            query += f" WHERE {q(GENERATION)}={lit(token(artifact))}"
        return query

    def __init__(
        self, database, objects, populations, managed, base_id, snapshots, edit
    ):
        fields = DecisionViewPlanner(objects, base_id).fields(edit.view)
        super().__init__(
            database,
            [*objects, virtual_level(fields)],
            populations,
            managed,
            base_id,
            snapshots.get(base_id) or next(iter(snapshots.values())),
        )
        self.relationships = ViewRelationships(objects, fields)
        self.levels[TARGET] = self.relationships.levels[TARGET]
        self.snapshots, self.edit, self.fields = (snapshots, edit, fields)
        self.write_id = MeasureEditPlanner(objects, base_id, fields).capability(
            edit.measureId, snapshots
        )["writeLevelId"]
        self.previous = snapshots[self.write_id]
        self.affected = {edit.measureId}
        changed = True
        while changed:
            changed = False
            for mid, measure in self.measures.items():
                d = measure["definition"]
                if (
                    d["measureType"] == "calculated"
                    and mid not in self.affected
                    and self.references(d["formula"]["expression"]) & self.affected
                ):
                    self.affected.add(mid)
                    changed = True
        self.edit_allocation = EditAllocationCompiler(self)
        self.edit_patch = EditPatchCompiler(self)
        self.cell_impact = EditCellImpact(self)
        self.count_argument_scopes = {}

    def disaggregate(self, parent, measure, source, target):
        scope = (
            None
            if self.cell_impact.suspended
            else self.cell_impact.scope_for(measure["objectId"], source)
        )
        parent = self.cell_impact.restrict_node(parent, scope, source)
        # Keep ALL children of changed parent groups in allocation denominators.
        child_scope = (
            self.cell_impact.map_keys(scope, source, target) if scope else None
        )
        result = self.edit_allocation.disaggregate(
            parent, measure, source, target, child_scope
        )
        target_scope = (
            None
            if self.cell_impact.suspended
            else self.cell_impact.scope_for(measure["objectId"], target)
        )
        return self.cell_impact.restrict_node(result, target_scope, target)

    def rollup(self, query, measure, source, target):
        scope = (
            None
            if self.cell_impact.suspended
            else self.cell_impact.scope_for(measure["objectId"], target)
        )
        if scope:
            contributors = self.cell_impact.map_keys(scope, target, source)
            query = self.cell_impact.restrict_node(query, contributors, source)
        result = super().rollup(query, measure, source, target)
        return self.cell_impact.restrict_node(result, scope, target)

    def output_population(self, target):
        scopes = [
            self.cell_impact.scope_for(mid, target)
            for mid in sorted(self.affected)
            if mid in self.snapshots.get(target, {}).get("measureColumns", {})
        ]
        scope = self.cell_impact.union_keys(scopes, target)
        population = self.population(target)
        return (
            self.cell_impact.restrict_query(population, scope, target)
            if scope
            else f"SELECT * FROM ({population}) WHERE 0"
        )

    def formula_count_cache_key(self, key, measure, target):
        return (
            *super().formula_count_cache_key(key, measure, target),
            measure["objectId"],
            bool(self.cell_impact.suspended),
        )

    def count_population(self, measure, source, target):
        population = self.population(source)
        if self.cell_impact.suspended:
            return population
        scope = self.cell_impact.scope_for(measure["objectId"], target)
        contributors = (
            self.cell_impact.map_keys(scope, target, source) if scope else None
        )
        return (
            self.cell_impact.restrict_query(population, contributors, source)
            if contributors
            else population
        )

    def compile_count_argument(self, measure, source, target):
        self.count_argument_scopes[id(measure)] = self.count_population(
            measure, source, target
        )
        try:
            # This temporary expression is an argument, not the owner's stored count result.
            return super().compile_calculated_measure(measure, source)
        finally:
            self.count_argument_scopes.pop(id(measure), None)

    def temporal_population(self, measure, target):
        population = self.population(target)
        if self.cell_impact.suspended:
            return population
        scope = self.cell_impact.scope_for(measure["objectId"], target)
        return (
            TemporalEditImpact(self.cell_impact).series_population(
                population, scope, target, measure["definition"]["temporal"]
            )
            if scope
            else population
        )

    def formula_population(self, measure, target):
        if self.temporal_input_depth:
            return self.temporal_population(measure, target)
        if id(measure) in self.count_argument_scopes:
            return self.count_argument_scopes[id(measure)]
        population = self.population(target)
        if not self.cell_impact.suspended:
            scope = self.cell_impact.scope_for(measure["objectId"], target)
            if scope:
                return self.cell_impact.restrict_query(population, scope, target)
        return population

    def compile_calculated_measure(self, measure, target):
        result = super().compile_calculated_measure(measure, target)
        return self.cell_impact.overlay(measure["objectId"], target, result)

    def quantize_node(self, query, measure, level):
        result = super().quantize_node(query, measure, level)
        if hasattr(self, "cell_impact") and query in self.cell_impact.full_nodes:
            self.cell_impact.full_nodes.add(result)
        return result

    def checkpoint_collections(self):
        return [
            self.cell_impact.scopes,
            self.cell_impact.mappings,
            self.cell_impact.full_nodes,
            self.edit_patch.state,
        ]

    def references(self, node):
        return set(measure_references(node))

    def stored(self, mid, level):
        if mid in self.snapshots.get(level, {}).get("measureColumns", {}):
            return self.stored_measure(mid, level, self.snapshots)
        measure = self.measures[mid]
        if measure["definition"].get("calculationType") == "AggregateFirst":
            return self.compile_calculated_measure(measure, level)
        native = measure["definition"]["nativeGrainId"]
        for source in dict.fromkeys([self.base_id, native]):
            if mid in self.snapshots.get(source, {}).get(
                "measureColumns", {}
            ) and self.relationships.analyze(source, level)["relationship"] in {
                "same",
                "equivalent",
                "roll_up",
            }:
                return self.rollup(
                    self.stored_measure(mid, source, self.snapshots),
                    measure,
                    source,
                    level,
                )
        return self.stored_measure(mid, level, self.snapshots)

    def compile_measure_at_level(self, mid, target):
        if self.cell_impact.suspended:
            return self.stored(mid, target)
        key = (mid, target)
        if key in self.cache:
            return self.cache[key]
        allowed = ("same", "equivalent", "roll_up")
        if mid not in self.affected or self.cell_impact.scope_for(mid, target) is None:
            result = self.stored(mid, target)
        elif mid == self.edit.measureId:
            if target == self.write_id:
                result = self.patch()
            elif (
                self.relationships.analyze(self.write_id, target)["relationship"]
                in allowed
            ):
                result = self.rollup(
                    self.compile_measure_at_level(mid, self.write_id),
                    self.measures[mid],
                    self.write_id,
                    target,
                )
            else:
                result = self.stored(mid, target)
        elif self.write_id != self.base_id:
            measure = self.measures[mid]
            d = measure["definition"]
            if (
                self.relationships.analyze(self.write_id, target)["relationship"]
                not in allowed
            ):
                result = self.stored(mid, target)
            elif d.get("calculationType") == "AggregateFirst":
                result = self.compile_calculated_measure(measure, target)
            else:
                native = d["nativeGrainId"]
                if (
                    self.relationships.analyze(self.write_id, native)["relationship"]
                    not in allowed
                    or self.relationships.analyze(native, target)["relationship"]
                    not in allowed
                ):
                    raise MetadataError(
                        "measure_path_unavailable",
                        "A dependent CalculateFirst measure cannot be recalculated through the native write path.",
                    )
                seed = self.compile_seed_measure(mid)
                result = (
                    seed
                    if target == native
                    else self.rollup(seed, measure, native, target)
                )
        else:
            self.cache.pop(key, None)
            result = super().compile_measure_at_level(mid, target)
        result = self.cell_impact.overlay(mid, target, result)
        result = self.quantize_node(result, self.measures[mid], target)
        self.cache[key] = result
        return result

    def compile_seed_measure(self, mid):
        if self.measures[mid]["definition"]["measureType"] == "referenced":
            return self.stored(mid, self.measures[mid]["definition"]["nativeGrainId"])
        return super().compile_seed_measure(mid)

    def patch(self):
        return self.edit_patch.patch()
