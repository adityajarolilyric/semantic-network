"""Derive calendar windows from declared semantic coordinates, without row reads."""

import re

from ...domain.dimensions import dimension_columns
from ...domain.typed_values import NUMERIC, result_type
from .relationships import LevelRelationshipAnalyzer


def period_unit(name):
    words = set(
        re.split(r"[^a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower())
    )
    for unit, aliases in (
        ("week", {"week", "weekly", "wk"}),
        ("month", {"month", "monthly"}),
        ("day", {"day", "date", "daily"}),
    ):
        if words & aliases:
            return unit
    return None


class TemporalContextResolver:
    def __init__(self, context):
        self.context = context
        self.relationships = LevelRelationshipAnalyzer(list(context.objects.values()))

    def resolve(self, level, explicit=None):
        columns = dimension_columns(level, list(self.context.objects.values()))
        names = {c["columnId"]: c["name"] for c in columns}
        keys = level["definition"]["grain"]["keyIds"]
        outputs = self.relationships.outputs(level["objectId"])
        time_keys = [key for key in keys if outputs[key].is_time]
        configured = level["definition"].get("timeColumnId")
        if configured in keys and configured not in time_keys:
            time_keys.append(configured)
        result = {
            "context": None,
            "summary": "",
            "reason": "",
            "columns": columns,
            "keys": [{"columnId": key, "name": names.get(key, key)} for key in keys],
            "timeKeyIds": time_keys,
        }

        def physical(column):
            return result_type(self.context.grain_type(level, column) or "")

        def finish(value):
            series = ", ".join(
                names.get(key, key) for key in value["partitionColumnIds"]
            )
            result.update(
                context=value,
                summary=f"Order by {names.get(value['orderColumnId'], value['orderColumnId'])}; "
                + (f"separate series for {series}." if series else "one series."),
            )
            slices = [
                key
                for key in time_keys
                if key
                not in {
                    value["timeKeyId"],
                    *value["partitionColumnIds"],
                    value.get("resetColumnId"),
                }
            ]
            if slices:
                result["summary"] += (
                    " Native calendar intersections follow their actual dates."
                )
            return result

        if explicit:
            return finish(explicit)
        if not time_keys:
            result["reason"] = (
                "No Time key is identified at this Output Dimension. Configure a Time Dimension or choose the time key in Advanced settings."
            )
            return result

        calendars = list(self.relationships.dimension_members.values())
        calendar_columns = {
            calendar["objectId"]: {
                column["columnId"]: column
                for column in calendar["definition"]["columns"]
            }
            for calendar in calendars
        }
        token_names = {}
        for calendar in calendars:
            if not calendar.get("isTimeDim"):
                continue
            for column in calendar["definition"]["columns"]:
                for token in (
                    ("dimensionMembers", calendar["objectId"], column["columnId"]),
                    (
                        "source",
                        calendar["definition"]["sourceId"],
                        column["sourceColumnId"],
                    ),
                ):
                    token_names.setdefault(token, set()).add(column["name"])

        def matches(coordinate, calendar, cid):
            column = calendar_columns[calendar["objectId"]].get(cid)
            return bool(
                column
                and coordinate.tokens
                & {
                    ("dimensionMembers", calendar["objectId"], cid),
                    (
                        "source",
                        calendar["definition"]["sourceId"],
                        column["sourceColumnId"],
                    ),
                }
            )

        def calendar_names(cid):
            return sorted(
                {
                    name
                    for token in outputs[cid].tokens
                    for name in token_names.get(token, ())
                }
            )

        def role(cid):
            units = {
                unit for name in calendar_names(cid) if (unit := period_unit(name))
            }
            return (
                next(iter(units))
                if len(units) == 1
                else period_unit(names.get(cid, cid))
                if not units
                else None
            )

        def unit(key):
            named = role(key)
            if named:
                return named
            coordinate = outputs[key]
            for calendar in calendars:
                if calendar.get("isTimeDim") and any(
                    matches(coordinate, calendar, cid)
                    for cid in calendar["definition"]["keyColumnIds"]
                ):
                    return calendar["definition"]["time"]["baseGrain"]
            dtype = physical(key)
            if dtype and dtype["type"] == "date":
                return "day"
            if key == configured and dtype and dtype["type"] in NUMERIC:
                return "index"
            return None

        ranked = [(key, unit(key)) for key in time_keys]
        if configured in time_keys:
            ranked = [(configured, unit(configured))]
        ranked = [(key, period) for key, period in ranked if period]
        if not ranked:
            result["reason"] = (
                "The Time key's calendar grain is ambiguous. Choose its period unit and ordering column in Advanced settings."
            )
            return result
        rank = {"day": 0, "index": 0, "week": 1, "month": 2}
        finest = min(rank[period] for _, period in ranked)
        choices = [(key, period) for key, period in ranked if rank[period] == finest]
        if len(choices) != 1:
            result["reason"] = (
                "More than one Time key can define this series. Choose the time key in Advanced settings."
            )
            return result
        key, period = choices[0]
        ordering = []
        for column in columns:
            cid = column["columnId"]
            dtype = physical(cid)
            if not dtype or not (
                outputs.get(cid) and (outputs[cid].is_time or cid == configured)
            ):
                continue
            if dtype["type"] == "date" and (cid == key or role(cid) == period):
                score = (
                    0
                    if cid == key
                    else 1
                    if any(
                        "start" in name.lower()
                        for name in [column["name"], *calendar_names(cid)]
                    )
                    else 2
                )
                ordering.append((score, cid, period))
            for calendar in calendars:
                if not calendar.get("isTimeDim"):
                    continue
                time = calendar["definition"]["time"]
                if time["baseGrain"] == period and matches(
                    outputs[cid], calendar, time["orderingColumnId"]
                ):
                    if dtype["type"] == "date" or dtype["type"] in NUMERIC:
                        ordering.append(
                            (0, cid, period if dtype["type"] == "date" else "index")
                        )
            if cid == key and period == "index" and dtype["type"] in NUMERIC:
                ordering.append((0, cid, period))
        ordering = sorted(set(ordering))
        best = (
            [choice for choice in ordering if choice[0] == ordering[0][0]]
            if ordering
            else []
        )
        if len(best) != 1:
            result["reason"] = (
                "A unique chronological ordering column is not available at this grain. Expose its period start date or choose an ordering column in Advanced settings."
            )
            return result
        _, order, period = best[0]
        return finish(
            {
                "timeKeyId": key,
                "orderColumnId": order,
                "periodUnit": period,
                "partitionColumnIds": [key for key in keys if key not in time_keys],
                "resetColumnId": None,
                "minimumPeriods": 1,
            }
        )
