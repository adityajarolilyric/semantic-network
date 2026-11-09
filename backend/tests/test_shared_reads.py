from sqlglot import exp, parse_one

from backend.app.engine.sql.shared_reads import combine_shared_reads
from backend.app.engine.sql.compilation_context import CompilationContext


def snapshot(column, generation="g"):
    return parse_one(
        f"SELECT k AS key, `{column}` AS __value, toUInt8(1) AS __complete FROM vals WHERE gen='{generation}'",
        read="clickhouse",
    )


def test_shared_snapshot_columns_keep_flags_and_one_join():
    query = parse_one(
        "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT p.key, x.__value AS A, y.__value AS B, y.__complete AS flag FROM pop p LEFT JOIN a x ON p.key=x.key LEFT JOIN b y ON p.key=y.key",
        read="clickhouse",
    )
    result = combine_shared_reads(query, {"a": snapshot("A"), "b": snapshot("B")})
    assert len(list(result.find_all(exp.Join))) == 1
    assert len([t for t in result.find_all(exp.Table) if t.name == "vals"]) == 1
    assert result.args.get("with") is None
    assert "__shared_complete_1" in result.sql()
    assert len(list(query.find_all(exp.Join))) == 2  # caller AST is immutable


def test_different_generations_or_join_predicates_are_not_combined():
    for other, on in [
        (snapshot("B", "new"), "p.key=y.key"),
        (snapshot("B"), "p.other=y.key"),
        (snapshot("B"), "p.key=y.__value"),
    ]:
        query = parse_one(
            f"SELECT x.__value,y.__value FROM pop p LEFT JOIN a x ON p.key=x.key LEFT JOIN b y ON {on}",
            read="clickhouse",
        )
        result = combine_shared_reads(query, {"a": snapshot("A"), "b": other})
        assert len(list(result.find_all(exp.Join))) == 2


def test_context_emits_only_reachable_ctes_and_keeps_qualified_tables_distinct():
    context = CompilationContext("db", [], {})
    used = context.cte("SELECT 1 AS x")
    unused = context.cte("SELECT 2 AS x")
    output = context.cte(f'SELECT x FROM "{used}"')
    sql = context.with_sql(f'SELECT * FROM "{output}"')
    assert unused not in sql
    assert used in sql and output in sql
    assert context.with_sql(f'SELECT * FROM "db"."{used}"').startswith("SELECT")
