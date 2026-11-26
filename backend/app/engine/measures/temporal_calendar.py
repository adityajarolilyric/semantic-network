"""Chronological calendar slices for cross-cutting native Time keys."""

from dataclasses import dataclass

from ...core.errors import MetadataError
from ...warehouse.sql import quote_identifier as q
from ..transformations.filters import dimension_filter_columns


SLICE_ORDER = "__temporal_slice_order"


@dataclass(frozen=True)
class TemporalPopulation:
    sql: str
    slice_order: str | None = None


def calendar_slice_keys(relationships, level, context):
    outputs = relationships.outputs(level)
    time_keys = [
        key
        for key in relationships.levels[level]["definition"]["grain"]["keyIds"]
        if outputs[key].is_time
    ]
    excluded = {
        context["timeKeyId"],
        *context["partitionColumnIds"],
        context.get("resetColumnId"),
    }
    if not any(key not in excluded for key in time_keys):
        return []
    keys = list(dict.fromkeys([context["timeKeyId"], *time_keys]))
    reset = context.get("resetColumnId")
    if reset in outputs and outputs[reset].is_time and reset not in keys:
        keys.append(reset)
    return keys


class TemporalCalendarCompiler:
    def __init__(self, compiler):
        self.compiler = compiler
        self.cache = {}

    def population(self, level, population, context, *, slices=True):
        c = self.compiler
        if not slices:
            return TemporalPopulation(population)
        outputs = c.relationships.outputs(level)
        key_ids = calendar_slice_keys(c.relationships, level, context)
        if not key_ids:
            return TemporalPopulation(population)
        cache_key = (level, population, tuple(key_ids))
        if cache_key in self.cache:
            return self.cache[cache_key]

        candidates = []
        for calendar in c.relationships.dimension_members.values():
            if not calendar.get("isTimeDim"):
                continue
            mappings = {}
            for key in key_ids:
                matches = [
                    column
                    for column in calendar["definition"]["columns"]
                    if outputs[key].tokens
                    & {
                        ("dimensionMembers", calendar["objectId"], column["columnId"]),
                        (
                            "source",
                            calendar["definition"]["sourceId"],
                            column["sourceColumnId"],
                        ),
                    }
                ]
                if len(matches) != 1:
                    break
                mappings[key] = matches[0]
            else:
                candidates.append((calendar, mappings))
        if len(candidates) != 1:
            raise MetadataError(
                "temporal_calendar_unavailable",
                "The native Time intersections need one calendar mapping for every Time key. Bind those keys to the Time Dimension before calculating calendar slices.",
            )
        calendar, mappings = candidates[0]
        definition = calendar["definition"]
        date = next(
            column
            for column in definition["columns"]
            if column["columnId"] == definition["time"]["dateColumnId"]
        )
        relation, columns = c.object_input(calendar, c.managed[definition["sourceId"]])
        names = {column["columnId"]: column["name"] for column in columns}
        needed = {
            date["columnId"],
            *(column["columnId"] for column in mappings.values()),
        }
        selected = [
            q(names[column["sourceColumnId"]]) + " AS " + q(column["columnId"])
            for column in definition["columns"]
            if column["columnId"] in needed or definition.get("filters")
        ]
        raw = f"SELECT {', '.join(selected)} FROM {relation}"
        if definition.get("filters"):
            raw = f"SELECT * FROM ({raw}) WHERE {c.filter_sql(definition['filters'], dimension_filter_columns(definition, columns))}"
        projected = [
            "d." + q(mappings[key]["columnId"]) + " AS " + q(key) for key in key_ids
        ]
        grouped = ", ".join("d." + q(mappings[key]["columnId"]) for key in key_ids)
        # MIN chooses the first actual date of a calendar intersection, never a
        # measure reducer. December/January and nonstandard calendars stay ordered.
        lookup = c.cte(
            f"SELECT {', '.join(projected)}, min(d.{q(date['columnId'])}) AS {q(SLICE_ORDER)},toUInt8(1) AS __calendar_match FROM ({raw}) d GROUP BY {grouped}"
        )
        on = " AND ".join(f"p.{q(key)}=d.{q(key)}" for key in key_ids)
        result = TemporalPopulation(
            f"SELECT p.*,if(coalesce(d.__calendar_match,0)=1,d.{q(SLICE_ORDER)},NULL) AS {q(SLICE_ORDER)} FROM ({population}) p LEFT JOIN {q(lookup)} d ON {on}",
            SLICE_ORDER,
        )
        self.cache[cache_key] = result
        return result
