from ...domain.grains import transaction_column_reference
from ...warehouse.sql import quote_identifier as q
from ...warehouse.sql import quote_literal as lit
from ..measures.contracts import COMPLETE, VALUE
from ..sql.contracts import Check


class EditPatchCompiler:
    """Compile write-grain patches and their affected measure representations."""

    def __init__(self, compiler):
        self.compiler = compiler
        self.state = {}

    @property
    def changed_keys(self):
        return self.state.get("keys")

    def patch(self):
        if "result" in self.state:
            return self.state["result"]
        mid = self.compiler.edit.measureId
        measure = self.compiler.measures[mid]
        old = self.compiler.stored(mid, self.compiler.write_id)
        enriched = self.compiler.enrich(
            old,
            self.compiler.write_id,
            {
                f["id"]: self.compiler.relationships.resolve(
                    self.compiler.write_id, f["coordinate"]
                )
                for f in self.compiler.fields
            },
        )
        predicates = []
        for field, value in zip(self.compiler.fields, self.compiler.edit.memberValues):
            if field.get("dimensionId") or field.get("transactionId"):
                grain_id, column_id = (field.get("dimensionId"), field["columnId"])
                if field.get("transactionId"):
                    grain_id, column_id = transaction_column_reference(
                        field["transactionId"],
                        field["columnId"],
                        list(self.compiler.objects.values()),
                    )
                population = self.compiler.populations[grain_id]
                typ = next(
                    (
                        c["physicalType"]
                        for c in population["columns"]
                        if c["columnId"] == column_id
                    )
                )
            else:
                dim = self.compiler.objects[field["memberCatalogId"]]["definition"]
                dc = next(
                    (c for c in dim["columns"] if c["columnId"] == field["columnId"])
                )
                source = self.compiler.managed[dim["sourceId"]]
                typ = next(
                    (
                        c["physicalType"]
                        for c in self.compiler.object_input(
                            self.compiler.objects[field["memberCatalogId"]], source
                        )[1]
                        if c["columnId"] == dc["sourceColumnId"]
                    )
                )
            literal = (
                "NULL"
                if value is None
                else lit(value)
                if "String" in typ
                else f"accurateCastOrNull({lit(value)},{lit(typ)})"
            )
            if value is not None and "String" not in typ:
                self.compiler.assertions.append(
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
        scope = self.compiler.cte(
            f"SELECT * FROM ({enriched}) WHERE {' AND '.join(predicates) or '1'}"
        )
        self.compiler.assertions.append(
            Check(
                "empty_edit_scope",
                f"SELECT count() AS matched FROM {q(scope)} HAVING matched=0",
            )
        )
        allocation = measure["definition"].get("allocation") or {"mode": "none"}
        mode = allocation["mode"]
        source = scope
        value = (
            self.compiler.literal(self.compiler.edit.value)
            if self.compiler.edit.value is not None
            else "CAST(NULL AS Nullable(Float64))"
        )
        if mode == "equal":
            value = self.compiler.divide(value, "count() OVER ()", measure)
        elif mode in ("weighted", "weighted_by_self"):
            basis_id = (
                mid if mode == "weighted_by_self" else allocation["basisMeasureId"]
            )
            basis = self.compiler.stored(basis_id, self.compiler.write_id)
            source = self.compiler.cte(
                f"SELECT c.*, w.{q(VALUE)} AS __edit_weight FROM {q(scope)} c LEFT JOIN {q(basis)} w ON {self.compiler.join_keys('c', 'w', self.compiler.write_id)}"
            )
            if mode == "weighted_by_self":
                populated = self.compiler.cte(
                    f"SELECT * FROM {q(source)} WHERE isNotNull(__edit_weight)"
                )
                self.compiler.assertions.append(
                    Check(
                        "empty_self_weights",
                        f"SELECT count() AS populated FROM {q(populated)} HAVING populated=0",
                    )
                )
                self.compiler.assertions.append(
                    Check(
                        "zero_self_weight_total",
                        f"SELECT sum(__edit_weight) AS total FROM {q(populated)} HAVING total=0",
                    )
                )
                source = populated
            total = "sum(__edit_weight) OVER ()"
            guard = f"throwIf(isNull(__edit_weight) OR {total}=0,'SEMANTIC_INVALID_WEIGHTS')"
            value = f"if(({guard})=0,({value})*({self.compiler.divide('__edit_weight', total, measure)}),NULL)"
        keys = ", ".join((q(k) for k in self.compiler.keys(self.compiler.write_id)))
        raw_patch = self.compiler.cte(
            f"SELECT {keys}, {value} AS {q(VALUE)} FROM {q(source)}"
        )
        rounded = self.compiler.quantize_node(
            raw_patch, measure, self.compiler.write_id
        )
        equal = f"ifNull(n.{q(VALUE)}=o.{q(VALUE)},isNull(n.{q(VALUE)}) AND isNull(o.{q(VALUE)}))"
        previous_complete = (
            f"o.{q(COMPLETE)}"
            if old in self.compiler.coverage_nodes or old in self.compiler.mixed_nodes
            else "1"
        )
        patch = self.compiler.cte(
            f"SELECT {', '.join('n.' + q(k) + ' AS ' + q(k) for k in self.compiler.keys(self.compiler.write_id))}, n.{q(VALUE)} AS {q(VALUE)}, toUInt8(1) AS __edited FROM {q(rounded)} n INNER JOIN {q(old)} o ON {self.compiler.join_keys('n', 'o', self.compiler.write_id)} WHERE NOT ({equal}) OR {previous_complete} != 1"
        )
        self.state["delta"] = patch
        self.state["keys"] = self.compiler.cte(f"SELECT {keys} FROM {q(patch)}")
        patched = self.compiler.cte(
            f"SELECT {', '.join(('o.' + q(k) + ' AS ' + q(k) for k in self.compiler.keys(self.compiler.write_id)))}, if(coalesce(p.__edited,0)=1,p.{q(VALUE)},o.{q(VALUE)}) AS {q(VALUE)}, if(coalesce(p.__edited,0)=1,toUInt8(1),o.{q(COMPLETE)}) AS {q(COMPLETE)} FROM {q(old)} o LEFT JOIN {q(patch)} p ON {self.compiler.join_keys('o', 'p', self.compiler.write_id)}"
        )
        if old in self.compiler.coverage_nodes:
            self.compiler.coverage_nodes[patched] = self.compiler.coverage_nodes[old]
        self.state["result"] = patched
        self.compiler.cell_impact.full_nodes.add(patched)
        return patched
