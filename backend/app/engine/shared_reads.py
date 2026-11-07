"""Coalesce compatible reads of one captured snapshot or native aggregation.

This is a physical-plan optimization only: grain, values, completeness flags,
join predicates and semantic validation remain unchanged.
"""

from collections import defaultdict

from sqlglot import exp

from .measure_contracts import COMPLETE, VALUE


def _scope_columns(select):
    return (
        column
        for column in select.find_all(exp.Column)
        if column.find_ancestor(exp.Select) is select
    )


def _key_join(join, alias, read):
    keys = {column.alias_or_name for column in read.expressions[:-2]}
    if not keys or [c.alias_or_name for c in read.expressions[-2:]] != [
        VALUE,
        COMPLETE,
    ]:
        return False
    predicate = join.args["on"]
    conditions = (
        list(predicate.flatten()) if isinstance(predicate, exp.And) else [predicate]
    )
    matched = []
    for condition in conditions:
        if not isinstance(condition, exp.EQ):
            return False
        left, right = condition.this, condition.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            return False
        if left.table == alias and right.table != alias:
            matched.append(left.name)
        elif right.table == alias and left.table != alias:
            matched.append(right.name)
        else:
            return False
    return len(matched) == len(keys) and set(matched) == keys


def combine_shared_reads(query, relations):
    query = query.copy()
    for select in list(query.find_all(exp.Select)):
        groups = defaultdict(list)
        for join in select.args.get("joins", []):
            relation = join.this
            read = (
                relations.get(relation.name)
                if isinstance(relation, exp.Table)
                else None
            )
            if read is None or join.side != "LEFT" or not join.args.get("on"):
                continue
            alias = relation.alias_or_name
            if not _key_join(join, alias, read):
                continue
            # Equal snapshot identity is insufficient if a join uses other keys.
            predicate = join.args["on"].copy()
            for column in predicate.find_all(exp.Column):
                if column.table == alias:
                    column.set("table", exp.to_identifier("__snapshot", quoted=True))
            source = read.copy()
            source.set("expressions", source.expressions[:-2])
            groups[(source.sql(), predicate.sql())].append((join, read))
        for group in groups.values():
            if len(group) < 2:
                continue
            first_join, first_read = group[0]
            alias = first_join.this.alias_or_name
            shared = first_read.copy()
            expressions = [column.copy() for column in first_read.expressions[:-2]]
            replacements = {}
            for index, (join, read) in enumerate(group):
                original_alias = join.this.alias_or_name
                for column in read.expressions[:-2]:
                    replacements[(original_alias, column.alias_or_name)] = (
                        column.alias_or_name
                    )
                for source, name in zip(read.expressions[-2:], (VALUE, COMPLETE)):
                    output = f"__shared_{name.strip('_')}_{index}"
                    expression = (
                        source.this if isinstance(source, exp.Alias) else source
                    )
                    expressions.append(expression.copy().as_(output, quoted=True))
                    replacements[(original_alias, name)] = output
            shared.set("expressions", expressions)
            first_join.set(
                "this",
                exp.Subquery(
                    this=shared,
                    alias=exp.TableAlias(this=exp.to_identifier(alias, quoted=True)),
                ),
            )
            for column in list(_scope_columns(select)):
                replacement = replacements.get((column.table, column.name))
                if replacement:
                    column.set("table", exp.to_identifier(alias, quoted=True))
                    column.set("this", exp.to_identifier(replacement, quoted=True))
            removed = {id(join) for join, _ in group[1:]}
            select.set(
                "joins", [j for j in select.args["joins"] if id(j) not in removed]
            )
    return prune_unused_ctes(query)


def prune_unused_ctes(query):
    """Drop unreachable DAG nodes after joins have been combined."""
    clause = query.args.get("with")
    if clause is None:
        return query
    definitions = {cte.alias: cte for cte in clause.expressions}
    root = query.copy()
    root.set("with", None)
    pending = [table.name for table in root.find_all(exp.Table)]
    used = set()
    while pending:
        name = pending.pop()
        if name not in definitions or name in used:
            continue
        used.add(name)
        pending.extend(table.name for table in definitions[name].find_all(exp.Table))
    retained = [cte for cte in clause.expressions if cte.alias in used]
    query.set("with", exp.With(expressions=retained) if retained else None)
    return query
