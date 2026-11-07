"""Build candidate populations and measure columns without touching live tables."""

from time import perf_counter

from ..core.errors import MetadataError
from .sql import quote_identifier as q


class CurrentTableBuilder:
    def __init__(self, executor):
        self.executor = executor

    async def build(self, artifact, compiled, plan):
        executor = self.executor
        storage = executor.storage
        started = perf_counter()
        settings = executor.execution_settings()
        columns = await storage.prepare(artifact, compiled, settings)
        population_query = artifact.get("populationQuery") or compiled.sql
        cloned = await storage.begin(
            artifact,
            columns,
            plan.key_ids,
            population_query,
            settings,
            preserve=artifact.get("writeMode") == "in_place",
        )
        has_measures = any(c.get("measureId") for c in columns)
        if has_measures:
            artifact.setdefault("writeMode", "column_build")
            artifact.setdefault(
                "updateColumnIds",
                [
                    c["columnId"]
                    for c in columns
                    if c.get("measureId") or c.get("coverageFor") or c.get("mixedFor")
                ],
            )
            artifact.setdefault(
                "measureOrder",
                list(
                    dict.fromkeys(c["measureId"] for c in columns if c.get("measureId"))
                ),
            )
            artifact.setdefault("measureNodes", {})
            outcome = await executor.in_place.build(
                artifact,
                compiled,
                plan,
                columns,
                settings,
                guard=storage.require_running,
            )
        else:

            def validate(connect):
                client = connect()
                table = executor.warehouse.qualify(artifact["viewName"])
                names = {c["columnId"]: c["name"] for c in columns}
                keys = ", ".join(q(names[k]) for k in plan.key_ids)
                count = int(
                    client.command(f"SELECT count() FROM {table}", settings=settings)
                )
                if count > executor.settings.planning_max_rows:
                    raise MetadataError(
                        "planning_row_limit",
                        "Population exceeds the configured row limit.",
                    )
                nulls = " OR ".join("isNull(" + q(names[k]) + ")" for k in plan.key_ids)
                if client.query(
                    f"SELECT {keys} FROM {table} WHERE {nulls} LIMIT 1",
                    settings=settings,
                ).result_rows:
                    raise MetadataError(
                        "null_planning_key", "Dimension grain keys cannot be NULL."
                    )
                if client.query(
                    f"SELECT {keys} FROM {table} GROUP BY {keys} HAVING count()>1 LIMIT 1",
                    settings=settings,
                ).result_rows:
                    raise MetadataError(
                        "duplicate_planning_key",
                        "The same dimension grain has conflicting output attributes.",
                    )
                return count

            count = await executor.warehouse.run(validate)
            outcome = {
                "rowCount": count,
                "columns": columns,
                "buildTimingsMs": {},
                "incompleteMeasures": {},
            }
        outcome["buildTimingsMs"].update(
            total=round((perf_counter() - started) * 1000, 3), cloned=cloned
        )
        outcome["sortKeyIds"] = artifact["sortKeyIds"]
        return outcome
