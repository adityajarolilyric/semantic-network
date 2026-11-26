"""Shared SQLGlot constructors; function names are supplied by engine registries."""

from sqlglot import exp


def function(name, *arguments):
    return exp.Anonymous(this=name, expressions=list(arguments))
