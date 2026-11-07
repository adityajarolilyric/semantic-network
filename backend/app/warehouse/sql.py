"""ClickHouse quoting primitives, independent of connections and repositories."""


def quote_identifier(value: str) -> str:
    return "`" + value.replace("\\", "\\\\").replace("`", "\\`") + "`"


def quote_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
