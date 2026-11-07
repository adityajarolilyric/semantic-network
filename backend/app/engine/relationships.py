from dataclasses import dataclass

from .contracts import digest
from .grain_contexts import resolve_grain_contexts


@dataclass(frozen=True)
class Coordinate:
    tokens: frozenset[tuple]
    column_id: str | None = None
    dimension_id: str | None = None
    dimension_column_id: str | None = None
    is_time: bool = False


class LevelRelationshipAnalyzer:
    """One metadata-only source of truth for reads, formula paths and edit eligibility."""

    def __init__(self, objects):
        objects = resolve_grain_contexts(objects)
        self.objects = dict(objects.by_id)
        self.levels = {
            k: o for k, o in self.objects.items() if o["kind"] in {"dimension", "grain"}
        }
        self.dimension_members = {
            k: o for k, o in self.objects.items() if o["kind"] == "dimensionMembers"
        }
        self._cache = {}

    def outputs(self, level_id, seen=None):
        if level_id in self._cache:
            return self._cache[level_id]
        seen = set() if seen is None else seen
        if level_id in seen or level_id not in self.levels:
            return {}
        definition = self.levels[level_id]["definition"]
        population = definition["population"]
        result = {}
        for column in [
            *definition["grain"]["columns"],
            *definition.get("populationAttributes", []),
        ]:
            cid = column.get("grainColumnId", column.get("columnId"))
            ref = column["populationRef"]
            tokens = {("level", level_id, cid)}
            if population["operation"] == "distinct_projection":
                parent = self.outputs(
                    population["sourceDimensionId"], seen | {level_id}
                )
                origin = parent.get(
                    ref.get("sourceGrainColumnId") or ref.get("sourceOutputColumnId")
                )
                if origin:
                    tokens |= set(origin.tokens)
            else:
                source = population.get("sourceId")
                if population["operation"] == "cartesian_product":
                    factor = next(
                        (
                            f
                            for f in population["factors"]
                            if f["factorId"] == ref.get("factorId")
                        ),
                        None,
                    )
                    source = factor["sourceId"] if factor else None
                    if sum(f["sourceId"] == source for f in population["factors"]) > 1:
                        source = level_id + ":" + ref["factorId"]
                if source:
                    tokens.add(("source", source, ref["columnId"]))
            if population["operation"] == "transaction":
                # Independent Transactions stay independent, even on the same source.
                tokens = {("level", level_id, cid)}
            result[cid] = Coordinate(
                frozenset(tokens),
                column_id=cid,
                is_time=cid == definition.get("timeColumnId"),
            )
        for binding in definition["memberBindings"]:
            dimension = self.dimension_members.get(binding["memberCatalogId"])
            if not dimension:
                continue
            dim_cols = {c["columnId"]: c for c in dimension["definition"]["columns"]}
            for mapping in binding["keyMappings"]:
                cid = mapping.get("grainColumnId") or mapping.get("populationColumnId")
                if cid not in result or mapping["memberColumnId"] not in dim_cols:
                    continue
                dc = dim_cols[mapping["memberColumnId"]]
                tokens = result[cid].tokens | {
                    ("dimensionMembers", dimension["objectId"], dc["columnId"]),
                    (
                        "source",
                        dimension["definition"]["sourceId"],
                        dc["sourceColumnId"],
                    ),
                }
                result[cid] = Coordinate(
                    frozenset(tokens), column_id=cid, is_time=dimension["isTimeDim"]
                )
            for dim_id in binding["selectedAttributeColumnIds"]:
                dc = dim_cols.get(dim_id)
                if dc:
                    cid = "da_" + digest([level_id, dimension["objectId"], dim_id])[:28]
                    result[cid] = Coordinate(
                        frozenset(
                            {
                                ("level", level_id, cid),
                                ("dimensionMembers", dimension["objectId"], dim_id),
                                (
                                    "source",
                                    dimension["definition"]["sourceId"],
                                    dc["sourceColumnId"],
                                ),
                            }
                        ),
                        column_id=cid,
                        is_time=dimension["isTimeDim"],
                    )
        # Time semantics also follow explicit source identity when Time is a population factor.
        for cid, coordinate in list(result.items()):
            time = coordinate.is_time or any(
                dim["isTimeDim"]
                and any(
                    ("source", dim["definition"]["sourceId"], c["sourceColumnId"])
                    in coordinate.tokens
                    for c in dim["definition"]["columns"]
                )
                for dim in self.dimension_members.values()
            )
            if time != coordinate.is_time:
                result[cid] = Coordinate(coordinate.tokens, column_id=cid, is_time=time)
        self._cache[level_id] = result
        return result

    def available(self, level_id):
        result = list(self.outputs(level_id).values())
        for binding in self.levels[level_id]["definition"]["memberBindings"]:
            dim = self.dimension_members.get(binding["memberCatalogId"])
            if not dim:
                continue  # Reference validation reports the missing dimension.
            mapped = {m["memberColumnId"] for m in binding["keyMappings"]}
            primary_key = set(dim["definition"].get("keyColumnIds", []))
            # A non-key binding validates membership and only promises its explicitly
            # selected enrichment attributes; Brand does not determine Product ID.
            known = mapped | set(binding["selectedAttributeColumnIds"])
            member_lookup = bool(primary_key) and primary_key <= mapped
            for col in dim["definition"]["columns"]:
                if not member_lookup and col["columnId"] not in known:
                    continue
                result.append(
                    Coordinate(
                        frozenset(
                            {
                                ("dimensionMembers", dim["objectId"], col["columnId"]),
                                (
                                    "source",
                                    dim["definition"]["sourceId"],
                                    col["sourceColumnId"],
                                ),
                            }
                        ),
                        dimension_id=dim["objectId"],
                        dimension_column_id=col["columnId"],
                        is_time=dim["isTimeDim"],
                    )
                )
        return result

    def resolve(self, source, target_coordinate):
        candidates = [
            c for c in self.available(source) if c.tokens & target_coordinate.tokens
        ]
        direct = [c for c in candidates if c.column_id]
        keys = self.levels[source]["definition"]["grain"]["keyIds"]
        key_matches = [c for c in direct if c.column_id in keys]
        preferred = key_matches or direct or candidates
        return preferred[0] if len(preferred) == 1 else None

    def mapping(self, source, target):
        outputs = self.outputs(target)
        return {
            key: self.resolve(source, outputs[key])
            for key in self.levels[target]["definition"]["grain"]["keyIds"]
        }

    def analyze(self, source, target):
        if source not in self.levels or target not in self.levels:
            return {
                "relationship": "incompatible",
                "reason": "Dimension does not exist.",
                "keyMappings": [],
            }
        forward, backward = self.mapping(source, target), self.mapping(target, source)
        forward_ok, backward_ok = all(forward.values()), all(backward.values())
        relation = (
            "same"
            if source == target
            else "equivalent"
            if forward_ok and backward_ok
            else "roll_up"
            if forward_ok
            else "roll_down"
            if backward_ok
            else "incompatible"
        )
        mapping = forward if forward_ok else backward if backward_ok else {}
        return {
            "relationship": relation,
            "sourceLevelId": source,
            "targetLevelId": target,
            "reason": None
            if relation != "incompatible"
            else "No proven key projection or required dimension binding connects these grains.",
            "keyMappings": [
                {
                    "targetKeyId": k,
                    "sourceColumnId": c.column_id,
                    "memberCatalogId": c.dimension_id,
                    "memberColumnId": c.dimension_column_id,
                }
                for k, c in mapping.items()
                if c
            ],
            "requiresDimensionLookup": any(
                c and c.dimension_id for c in mapping.values()
            ),
        }

    def expansion_axes(self, source, target):
        """Axes whose finer target keys cannot be supplied by the source grain."""
        outputs = self.outputs(target)
        added = [
            k
            for k in self.levels[target]["definition"]["grain"]["keyIds"]
            if self.resolve(source, outputs[k]) is None
        ]
        return [
            axis
            for axis in ("nonTime", "time")
            if any(outputs[k].is_time == (axis == "time") for k in added)
        ]

    def can_edit(self, measure, target):
        relation = self.analyze(
            measure["definition"].get("nativeGrainId")
            or measure["definition"].get("nativeDimensionId"),
            target,
        )
        return bool(
            measure["definition"].get("editable")
            and measure["definition"].get("resultType", {"type": "number"})["type"]
            in {"number", "integer", "decimal"}
            and relation["relationship"] in ("same", "equivalent", "roll_up")
        )
