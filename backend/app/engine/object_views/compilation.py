"""Object views refer to physical inputs when managed definitions are identity."""

from dataclasses import replace
from sqlglot import exp, parse_one
from ..sql.contracts import Check


def direct_identity_inputs(compiled, objects, managed, definitions):
    by_id = {o["objectId"]: o for o in objects}
    replacements = {
        a["physicalName"]: by_id[sid]["definition"]["tableName"]
        for sid, a in managed.items()
        if sid in definitions
        and sid in by_id
        and by_id[sid]["kind"] == "source"
        and not definitions[sid]["definition"].get("pipeline")
    }

    def transform(sql):
        query = parse_one(sql, read="clickhouse")
        for table in query.find_all(exp.Table):
            if table.db and table.name in replacements:
                table.set(
                    "this", exp.to_identifier(replacements[table.name], quoted=True)
                )
        return query

    return replace(
        compiled,
        query=transform(compiled.sql),
        checks=tuple(
            Check(c.code, transform(c.sql).sql(dialect="clickhouse"))
            for c in compiled.checks
        ),
    )
