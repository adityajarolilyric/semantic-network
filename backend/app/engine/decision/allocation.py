from ...warehouse.sql import quote_identifier as q
from ..measures.contracts import COMPLETE, VALUE
from ..sql.contracts import Check


class EditAllocationCompiler:
    """Compile configured edit distribution using captured writable values."""

    def __init__(self, compiler):
        self.compiler = compiler

    def disaggregate(self, parent, measure, source, target, child_scope=None):
        mapping = self.compiler.relationships.mapping(target, source)
        population = self.compiler.population(target)
        if child_scope:
            keys = ", ".join(q(k) for k in self.compiler.keys(target))
            population = f"SELECT * FROM ({population}) WHERE tuple({keys}) IN (SELECT tuple({keys}) FROM {q(child_scope)})"
        child = self.compiler.cte(
            "SELECT "
            + ", ".join((q(k) for k in self.compiler.keys(target)))
            + f", NULL AS {q(VALUE)} FROM ({population})"
        )
        enriched = self.compiler.enrich(
            child, target, {"__parent_" + k: c for k, c in mapping.items()}
        )
        child = self.compiler.cte(enriched)
        on = " AND ".join(
            (
                "c." + q("__parent_" + k) + " = p." + q(k)
                for k in self.compiler.keys(source)
            )
        )
        partition = ", ".join(
            ("c." + q("__parent_" + k) for k in self.compiler.keys(source))
        )
        policy = measure["definition"].get("allocation") or {"mode": "none"}
        mode = policy["mode"]
        weight_join = ""
        weight = None
        if mode == "weighted":
            basis = self.compiler.compile_measure_at_level(
                policy["basisMeasureId"], target
            )
            weight_join = f"LEFT JOIN {q(basis)} w ON " + self.compiler.join_keys(
                "c", "w", target
            )
            weight = f"w.{q(VALUE)}"
        elif mode == "weighted_by_self":
            old = self.compiler.previous
            if (
                old
                and measure["objectId"] in old.get("measureColumns", {})
                and (old["levelId"] == target)
                and (set(old["keyColumns"]) == set(self.compiler.keys(target)))
            ):
                weight_join = (
                    f"LEFT JOIN ({self.compiler.previous_values_sql(old)}) w ON "
                    + " AND ".join(
                        (
                            "c." + q(k) + " = w." + q(old["keyColumns"][k])
                            for k in self.compiler.keys(target)
                        )
                    )
                )
                weight = "w." + q(old["measureColumns"][measure["objectId"]])
            else:
                weight = "CAST(NULL AS Nullable(Float64))"
        if weight and measure["definition"]["resultType"]["type"] == "number":
            weight = f"toFloat64({weight})"
        value = "p." + q(VALUE)
        if mode == "equal":
            value = self.compiler.divide(
                value, f"count() OVER (PARTITION BY {partition})", measure
            )
        eligible = "1"
        # NULL weights are ineligible; eligible groups still require a nonzero total.
        if weight:
            denominator = f"sum({weight}) OVER (PARTITION BY {partition})"
            eligible = f"coalesce(isNotNull({weight}) AND {denominator} != 0, 0)"
            value = f"if({eligible},({value}) * ({self.compiler.divide(weight, denominator, measure)}),NULL)"
        grouped = ", ".join((q("__parent_" + k) for k in self.compiler.keys(source)))
        cover_on = " AND ".join(
            (
                "p." + q(k) + " = c." + q("__parent_" + k)
                for k in self.compiler.keys(source)
            )
        )
        self.compiler.assertions.append(
            Check(
                "no_disaggregation_children",
                f"SELECT {', '.join(('p.' + q(k) for k in [*self.compiler.keys(source), VALUE]))} FROM {q(parent)} p LEFT JOIN (SELECT {grouped},toUInt8(1) AS __matched FROM {q(child)} GROUP BY {grouped}) c ON {cover_on} WHERE coalesce(c.__matched,0)=0 AND isNotNull(p.{q(VALUE)}) LIMIT 20",
            )
        )
        body = f"FROM {q(child)} c LEFT JOIN {q(parent)} p ON {on} {weight_join}"
        keys = ", ".join(
            ("c." + q(k) + " AS " + q(k) for k in self.compiler.keys(target))
        )
        flag = (
            f"toUInt8(isNull(p.{q(VALUE)}) OR ({eligible}))" if weight else "toUInt8(1)"
        )
        if parent in self.compiler.coverage_nodes:
            flag = f"toUInt8(({flag}) AND coalesce(p.{q(COMPLETE)},0))"
        result = self.compiler.cte(
            f"SELECT {keys}, {value} AS {q(VALUE)}, {flag} AS {q(COMPLETE)} {body}"
        )
        if weight or parent in self.compiler.coverage_nodes:
            self.compiler.coverage_nodes[result] = True
        return result
