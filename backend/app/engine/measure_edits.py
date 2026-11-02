"""Base-or-native writeback against a captured generation, without replay storage."""

from ..core.errors import MetadataError
from ..warehouse.repository import quote_identifier as q, quote_literal as lit
from .contracts import Check
from .measure_compiler import MeasureCompiler, VALUE
from .planning_view import PlanningViewPlanner, ViewRelationships, TARGET, virtual_level
from .table_storage import GENERATION, column_sql, token


class MeasureEditPlanner:
    def __init__(self, objects, base_id, fields):
        self.objects = {o["objectId"]: o for o in objects}
        self.base_id, self.fields = base_id, fields
        self.relationships = ViewRelationships(objects, fields)

    def capability(self, mid, snapshots, draft=True):
        d = self.objects[mid]["definition"]
        allowed = ("same", "equivalent", "roll_up")
        native = d["nativePlanningLevelId"]
        anchor = (
            self.base_id
            if all(
                self.relationships.analyze(self.base_id, target)["relationship"]
                in allowed
                for target in (native, TARGET)
            )
            else native
        )
        artifact = snapshots.get(anchor, {})
        reason = None
        if not draft:
            reason = "Published revisions are read-only."
        elif d["measureType"] != "referenced":
            reason = "Calculated measures are derived; edit an input measure instead."
        elif not d.get("editable"):
            reason = "This measure is not marked editable."
        elif self.relationships.analyze(native, TARGET)["relationship"] not in allowed:
            reason = (
                "Editing is allowed only at the native grain or a proven coarser grain."
            )
        elif mid not in artifact.get("measureColumns", {}):
            reason = "Materialise this measure at its write level before editing."
        elif (d.get("allocation") or {}).get("mode") == "weighted" and d["allocation"][
            "basisMeasureId"
        ] not in artifact.get("measureColumns", {}):
            reason = "Materialise the disaggregation basis at the write level before editing."
        return {
            "editable": reason is None,
            "editReason": reason,
            "writeLevelId": anchor,
            "writeLevelName": self.objects[anchor].get("name", anchor),
            "writesToBase": anchor == self.base_id,
        }

    def validate(self, payload, snapshots, draft=True):
        if (
            payload.measureId not in self.objects
            or self.objects[payload.measureId]["kind"] != "measure"
        ):
            raise MetadataError(
                "invalid_edit_measure", "The selected measure does not exist."
            )
        capability = self.capability(payload.measureId, snapshots, draft)
        if not capability["editable"]:
            raise MetadataError("measure_not_editable", capability["editReason"])
        if (
            payload.value is None
            and not self.objects[payload.measureId]["definition"]["resultType"][
                "nullable"
            ]
        ):
            raise MetadataError(
                "edit_null_rejected", "This measure does not allow NULL values."
            )


class MeasureEditCompiler(MeasureCompiler):
    def __init__(
        self, database, objects, populations, managed, base_id, snapshots, edit
    ):
        fields = PlanningViewPlanner(objects, base_id).fields(edit.view)
        super().__init__(
            database,
            [*objects, virtual_level(fields)],
            populations,
            managed,
            base_id,
            snapshots[base_id],
        )
        self.relationships = ViewRelationships(objects, fields)
        self.snapshots, self.edit, self.fields = snapshots, edit, fields
        self.write_id = MeasureEditPlanner(objects, base_id, fields).capability(
            edit.measureId, snapshots
        )["writeLevelId"]
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

    def references(self, node):
        if node["type"] == "measureRef":
            return {node["measureId"]}
        if node["type"] == "binary":
            return self.references(node["left"]) | self.references(node["right"])
        if node["type"] == "call":
            return set().union(*(self.references(c) for c in node["arguments"]))
        return set()

    def stored(self, mid, level):
        artifact = self.snapshots.get(level, {})
        if mid not in artifact.get("measureColumns", {}):
            raise MetadataError(
                "measure_path_unavailable",
                "This measure was not materialised at this level in the captured generation.",
            )
        column = next(c for c in artifact["columns"] if c.get("measureId") == mid)
        selected = [
            q(artifact["keyColumns"][k]) + " AS " + q(k) for k in self.keys(level)
        ]
        selected.append(column_sql(column) + " AS " + q(VALUE))
        return self.cte(
            f"SELECT {', '.join(selected)} FROM {self.table(artifact['physicalName'])} WHERE {q(GENERATION)}={lit(token(artifact))}"
        )

    def node(self, mid, target):
        key = (mid, target)
        if key in self.cache:
            return self.cache[key]
        allowed = ("same", "equivalent", "roll_up")
        if mid not in self.affected:
            result = self.stored(mid, target)
        elif mid == self.edit.measureId:
            if target == self.write_id:
                result = self.patch()
            elif (
                self.relationships.analyze(self.write_id, target)["relationship"]
                in allowed
            ):
                result = self.rollup(
                    self.node(mid, self.write_id),
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
                result = self.calculated(measure, target)
            else:
                native = d["nativePlanningLevelId"]
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
                seed = self.seed(mid)
                result = (
                    seed
                    if target == native
                    else self.rollup(seed, measure, native, target)
                )
        else:
            return super().node(mid, target)
        self.cache[key] = result
        return result

    def seed(self, mid):
        if self.measures[mid]["definition"]["measureType"] == "referenced":
            return self.stored(
                mid, self.measures[mid]["definition"]["nativePlanningLevelId"]
            )
        return super().seed(mid)

    def patch(self):
        mid = self.edit.measureId
        measure = self.measures[mid]
        old = self.stored(mid, self.write_id)
        enriched = self.enrich(
            old,
            self.write_id,
            {
                f["id"]: self.relationships.resolve(self.write_id, f["coordinate"])
                for f in self.fields
            },
        )
        predicates = []
        for field, value in zip(self.fields, self.edit.memberValues):
            dim = self.objects[field["dimensionId"]]["definition"]
            dc = next(c for c in dim["columns"] if c["columnId"] == field["columnId"])
            source = self.managed[dim["sourceId"]]
            typ = next(
                c["physicalType"]
                for c in source["columns"]
                if c["columnId"] == dc["sourceColumnId"]
            )
            literal = (
                "NULL"
                if value is None
                else lit(value)
                if "String" in typ
                else f"accurateCastOrNull({lit(value)},{lit(typ)})"
            )
            if value is not None and "String" not in typ:
                self.assertions.append(
                    Check(
                        "invalid_edit_member",
                        f"SELECT {literal} WHERE isNull({literal})",
                    )
                )
            predicates.append(
                f"isNull({q(field['id'])})"
                if value is None
                else f"coalesce({q(field['id'])}={literal},0)"
            )
        scope = self.cte(
            f"SELECT * FROM ({enriched}) WHERE {' AND '.join(predicates) or '1'}"
        )
        self.assertions.append(
            Check(
                "empty_edit_scope",
                f"SELECT count() AS matched FROM {q(scope)} HAVING matched=0",
            )
        )
        allocation = measure["definition"].get("allocation") or {"mode": "none"}
        mode = allocation["mode"]
        source = scope
        value = (
            self.literal(self.edit.value)
            if self.edit.value is not None
            else "CAST(NULL AS Nullable(Float64))"
        )
        if mode == "equal":
            value = self.divide(value, "count() OVER ()", measure)
        elif mode in ("weighted", "weighted_by_self"):
            basis_id = (
                mid if mode == "weighted_by_self" else allocation["basisMeasureId"]
            )
            basis = self.stored(basis_id, self.write_id)
            source = self.cte(
                f"SELECT c.*, w.{q(VALUE)} AS __edit_weight FROM {q(scope)} c LEFT JOIN {q(basis)} w ON {self.join_keys('c', 'w', self.write_id)}"
            )
            if mode == "weighted_by_self":
                # Missing transaction cells do not establish a distribution pattern.
                # Preserve their NULL values instead of assigning an invented share.
                populated = self.cte(
                    f"SELECT * FROM {q(source)} WHERE isNotNull(__edit_weight)"
                )
                self.assertions.append(
                    Check(
                        "empty_self_weights",
                        f"SELECT count() AS populated FROM {q(populated)} HAVING populated=0",
                    )
                )
                self.assertions.append(
                    Check(
                        "zero_self_weight_total",
                        f"SELECT sum(__edit_weight) AS total FROM {q(populated)} HAVING total=0",
                    )
                )
                source = populated
            total = "sum(__edit_weight) OVER ()"
            guard = f"throwIf(isNull(__edit_weight) OR {total}=0,'SEMANTIC_INVALID_WEIGHTS')"
            value = f"if(({guard})=0,({value})*({self.divide('__edit_weight', total, measure)}),NULL)"
        keys = ", ".join(q(k) for k in self.keys(self.write_id))
        patch = self.cte(
            f"SELECT {keys}, {value} AS {q(VALUE)}, toUInt8(1) AS __edited FROM {q(source)}"
        )
        patched = self.cte(
            f"SELECT {', '.join('o.' + q(k) + ' AS ' + q(k) for k in self.keys(self.write_id))}, if(coalesce(p.__edited,0)=1,p.{q(VALUE)},o.{q(VALUE)}) AS {q(VALUE)} FROM {q(old)} o LEFT JOIN {q(patch)} p ON {self.join_keys('o', 'p', self.write_id)}"
        )
        return patched
