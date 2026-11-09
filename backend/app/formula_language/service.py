"""Stateless language facade. Parsing/resolution never accesses persistence."""

from collections import defaultdict
from dataclasses import asdict

from ..core.errors import MetadataError
from ..domain.formula_types import attribute_physical_type, infer
from ..validation.graph import SemanticGraphValidator
from . import AST_VERSION, LANGUAGE_VERSION
from .catalog import FUNCTION_CATALOG
from .expressions import (
    contains_counts,
    contains_temporal,
    definition_references,
    conversion_factor_references,
)
from .parser import formula_parser
from .resolver import FormulaResolver


class FormulaLanguage:
    @staticmethod
    def catalog():
        return {
            "languageVersion": LANGUAGE_VERSION,
            "astVersion": AST_VERSION,
            "functions": [
                {**asdict(item), "group": item.group}
                for item in FUNCTION_CATALOG
                if item.category != "reference"
            ],
        }

    @staticmethod
    def resolve(text, objects, native):
        syntax = formula_parser().parse(text)
        return FormulaResolver(objects, native).resolve(syntax)

    @classmethod
    def validate(cls, payload, objects, base_level=None):
        result = {
            "valid": False,
            "languageVersion": LANGUAGE_VERSION,
            "astVersion": AST_VERSION,
            "diagnostics": [],
            "containsCounts": False,
            "containsTemporal": False,
        }
        try:
            name = payload.name
            if payload.requireAssignment:
                parsed = formula_parser().parse_definition(payload.displayText)
                name = parsed.name
                expression = FormulaResolver(objects, payload.nativeGrainId).resolve(
                    parsed.expression
                )
                result.update(name=name, expressionText=parsed.expression_text)
            else:
                expression = cls.resolve(
                    payload.displayText, objects, payload.nativeGrainId
                )
            result.update(
                expression=expression,
                containsCounts=contains_counts(expression),
                containsTemporal=contains_temporal(expression),
                containsConversionFactors=bool(
                    conversion_factor_references(
                        expression, {obj["objectId"]: obj for obj in objects}
                    )
                ),
            )
            owner = {
                "objectId": payload.objectId,
                "kind": "measure",
                "name": name,
                "business": {"unit": None},
                "definition": {
                    "measureType": "calculated",
                    "nativeGrainId": payload.nativeGrainId,
                    "calculationType": payload.calculationType,
                    "temporal": payload.temporal.model_dump()
                    if payload.temporal
                    else None,
                    "formula": {"expression": expression},
                    "resultType": {"type": "number", "nullable": True},
                },
            }
            context = SemanticGraphValidator([*objects, owner])
            context.base = base_level

            context.edges = defaultdict(set)
            level = context.objects.get(payload.nativeGrainId)
            if not level or level["kind"] not in {"dimension", "grain"}:
                raise MetadataError("formula_level", "Choose an output dimension.")
            kind = infer(
                expression,
                lambda mid: context.objects[mid]["definition"]["resultType"]["type"],
                lambda node: attribute_physical_type(context, owner, node),
            )
            context.formulas_rules.validate_counting(owner)
            context.validate_expression(owner, expression, level, 0)
            # Include existing measure edges so editing A to reference B detects
            # an indirect cycle when B already depends on A.
            for obj in objects:
                if obj["kind"] != "measure" or obj["objectId"] == owner["objectId"]:
                    continue
                definition = obj["definition"]
                for dependency in definition_references(definition):
                    context.edges[obj["objectId"]].add(dependency)
                basis = (definition.get("allocation") or {}).get("basisMeasureId")
                if basis:
                    context.edges[obj["objectId"]].add(basis)
            context.validate_cycles()
            result.update(
                unit=context.expression_unit(owner, expression),
                resultType={"type": kind, "nullable": True},
                diagnostics=[d.model_dump() for d in context.errors],
                valid=not context.errors,
            )
        except MetadataError as exc:
            result["diagnostics"] = exc.details or [
                {"code": exc.code, "message": exc.message}
            ]
        return result
