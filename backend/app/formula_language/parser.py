"""A cached LALR grammar; each parse returns request-local syntax nodes."""

import json
import math
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from lark import Lark, Transformer_NonRecursive, UnexpectedInput, v_args
from lark.exceptions import VisitError

from ..core.errors import MetadataError


class SyntaxTree(Transformer_NonRecursive):
    def reference(self, children):
        token = children[0]
        text = str(token)
        return {
            "type": "symbol",
            "name": text[1:-1].replace("]]", "]")
            if token.type == "BRACKET_NAME"
            else text,
            "line": token.line,
            "column": token.column,
        }

    def number(self, children):
        text = str(children[0])
        value = float(text) if any(ch in text.lower() for ch in ".e") else int(text)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Numeric literals must be finite.")
        return {"type": "literal", "value": value}

    def string(self, children):
        return {"type": "literal", "value": json.loads(str(children[0]))}

    def true(self, children):
        return {"type": "literal", "value": True}

    def false(self, children):
        return {"type": "literal", "value": False}

    def positive(self, children):
        return children[0]

    def negate(self, children):
        return {
            "type": "binary",
            "operator": "subtract",
            "left": {"type": "literal", "value": 0},
            "right": children[0],
        }

    def arguments(self, children):
        return list(children)

    def function(self, children):
        token = children[0]
        return {
            "type": "function",
            "name": str(token).lower(),
            "arguments": children[1]
            if len(children) > 1 and children[1] is not None
            else [],
            "line": token.line,
            "column": token.column,
        }

    @staticmethod
    def fold(children, operators):
        node = children[0]
        for index in range(1, len(children), 2):
            node = {
                "type": "binary",
                "operator": operators[str(children[index])],
                "left": node,
                "right": children[index + 1],
            }
        return node

    def comparison(self, children):
        return self.fold(
            children,
            {
                "=": "eq",
                "!=": "ne",
                "<>": "ne",
                ">": "gt",
                ">=": "gte",
                "<": "lt",
                "<=": "lte",
            },
        )

    def sum(self, children):
        return self.fold(children, {"+": "add", "-": "subtract"})

    def product(self, children):
        return self.fold(children, {"*": "multiply", "/": "divide"})


@dataclass(frozen=True)
class ParsedFormulaDefinition:
    name: str
    expression_text: str
    expression: dict


class DefinitionSyntaxTree(SyntaxTree):
    def __init__(self, source):
        super().__init__()
        self.source = source

    def definition_name(self, children):
        token = children[0]
        raw = str(token)
        name = (
            json.loads(raw)
            if token.type == "ESCAPED_STRING"
            else raw[1:-1].replace("]]", "]")
            if token.type == "BRACKET_NAME"
            else raw
        )
        if any(unicodedata.category(char) in {"Cc", "Cs"} for char in name):
            raise MetadataError(
                "formula_name", "Measure names cannot contain control characters."
            )
        name = name.strip()
        if not name or len(name) > 200:
            raise MetadataError(
                "formula_name", "Use a measure name of 1–200 characters before =."
            )
        return name

    @v_args(meta=True)
    def definition_expression(self, meta, children):
        return self.source[meta.start_pos : meta.end_pos].strip(), children[0]

    def definition(self, children):
        name, (text, expression) = children
        return ParsedFormulaDefinition(name, text, expression)


class FormulaParser:
    def __init__(self):
        self.parser = Lark(
            files(__package__).joinpath("grammar.lark").read_text(),
            parser="lalr",
            start=["start", "definition"],
            lexer="contextual",
            propagate_positions=True,
            maybe_placeholders=False,
        )

    def parse(self, text):
        return self._parse(text, "start", SyntaxTree())

    def parse_definition(self, text):
        return self._parse(text, "definition", DefinitionSyntaxTree(text))

    def _parse(self, text, start, transformer):
        if not text.strip() or len(text) > 4000:
            raise MetadataError(
                "formula_syntax", "Enter a formula of 1–4,000 characters."
            )
        try:
            return transformer.transform(self.parser.parse(text, start=start))
        except VisitError as error:
            if isinstance(error.orig_exc, MetadataError):
                raise error.orig_exc from error
            raise MetadataError(
                "formula_literal", "Use valid JSON strings and finite numeric literals."
            ) from error
        except UnexpectedInput as error:
            raise MetadataError(
                "formula_syntax",
                f"Formula syntax error at line {error.line}, column {error.column}.",
                details=[
                    {
                        "line": error.line,
                        "column": error.column,
                        "message": error.get_context(text, span=30).strip(),
                    }
                ],
            ) from error


@lru_cache(maxsize=1)
def formula_parser():
    return FormulaParser()
