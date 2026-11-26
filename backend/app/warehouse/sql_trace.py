"""Bounded, operation-local recording of executed warehouse statements."""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from clickhouse_connect.driver.binding import finalize_query, format_query_value
from sqlglot import Dialect
from sqlglot.tokens import TokenType

from .sql import quote_identifier

_current_trace = ContextVar("warehouse_sql_trace", default=None)


def current_trace():
    return _current_trace.get()


@dataclass
class SqlTrace:
    max_bytes: int
    statements: list[dict] = field(default_factory=list)
    omitted: int = 0
    used_bytes: int = 0

    def record(self, method, sql, parameters, settings):
        item = {
            "method": method,
            "sql": sql,
            "parameters": parameters or {},
            "settings": settings or {},
            "state": "submitted",
        }
        size = len(json.dumps(item, default=str).encode())
        if self.used_bytes + size > self.max_bytes or len(self.statements) >= 512:
            self.omitted += 1
            return None
        self.used_bytes += size
        self.statements.append(item)
        return item


@contextmanager
def capture_sql(max_bytes):
    trace = SqlTrace(max_bytes)
    token = _current_trace.set(trace)
    try:
        yield trace
    finally:
        _current_trace.reset(token)


class RecordingClient:
    """A per-operation facade over the existing shared client, never a new connection."""

    def __init__(self, client, trace):
        self.client, self.trace = client, trace

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _execute(self, method, sql, *args, **kwargs):
        parameters = kwargs.get("parameters", args[0] if args else None)
        item = self.trace.record(method, sql, parameters, kwargs.get("settings"))
        try:
            result = getattr(self.client, method)(sql, *args, **kwargs)
        except Exception as error:
            if item is not None:
                item.update(state="failed", error=str(error)[:500])
            raise
        if item is not None:
            item["state"] = "succeeded"
        return result

    def query(self, sql, *args, **kwargs):
        return self._execute("query", sql, *args, **kwargs)

    def command(self, sql, *args, **kwargs):
        return self._execute("command", sql, *args, **kwargs)


def bound_statement(item):
    """Render native parameters as typed literals without touching quoted SQL text."""
    sql, parameters = item["sql"], item.get("parameters")
    if not parameters:
        return sql
    tokens = Dialect.get_or_raise("clickhouse").tokenize(sql)
    replacements = []
    for index, token in enumerate(tokens[:-3]):
        if (
            token.token_type != TokenType.L_BRACE
            or tokens[index + 2].token_type != TokenType.COLON
        ):
            continue
        name = tokens[index + 1].text
        if not isinstance(parameters, dict) or name not in parameters:
            continue
        closing = next(
            (t for t in tokens[index + 3 :] if t.token_type == TokenType.R_BRACE), None
        )
        if closing is None:
            continue
        dtype = sql[tokens[index + 2].end + 1 : closing.start].strip()
        value = (
            quote_identifier(parameters[name])
            if dtype.lower() == "identifier"
            else f"CAST({format_query_value(parameters[name])} AS {dtype})"
        )
        replacements.append((token.start, closing.end + 1, value))
    if not replacements:
        return finalize_query(sql, parameters)
    for start, end, value in reversed(replacements):
        sql = sql[:start] + value + sql[end:]
    return sql


def trace_script(statements):
    parts = []
    for index, item in enumerate(statements, 1):
        settings = item.get("settings") or {}
        setting_sql = (
            "SET "
            + ", ".join(
                quote_identifier(k) + "=" + str(format_query_value(v))
                for k, v in settings.items()
            )
            + ";\n"
            if settings
            else ""
        )
        parts.append(
            f"-- Statement {index}: {item['method']} ({item['state']})\n"
            + setting_sql
            + bound_statement(item).rstrip(";")
            + ";"
        )
    return "\n\n".join(parts)
