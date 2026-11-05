"""Stateless language facade. Parsing/resolution never accesses persistence."""

from dataclasses import asdict

from ..core.errors import MetadataError
from ..domain.formula_types import infer, attribute_physical_type
from ..validation.graph import SemanticGraphValidator
from . import AST_VERSION, LANGUAGE_VERSION
from .catalog import FUNCTION_CATALOG
from .expressions import contains_counts, walk
from .parser import formula_parser
from .resolver import FormulaResolver


class FormulaLanguage:
    @staticmethod
    def catalog():
        return {
            "languageVersion": LANGUAGE_VERSION,
            "astVersion": AST_VERSION,
            "functions": [
                asdict(item)
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
        }
        try:
            expression = cls.resolve(
                payload.displayText,
                objects,
                payload.nativePlanningLevelId,
            )
            result.update(
                expression=expression, containsCounts=contains_counts(expression)
            )
            owner = {
                "objectId": payload.objectId,
                "kind": "measure",
                "name": payload.name,
                "business": {"unit": None},
                "definition": {
                    "measureType": "calculated",
                    "nativePlanningLevelId": payload.nativePlanningLevelId,
                    "calculationType": payload.calculationType,
                    "formula": {"expression": expression},
                    "resultType": {"type": "number", "nullable": True},
                },
            }
            context = SemanticGraphValidator([*objects, owner])
            context.base = base_level
            from collections import defaultdict

            context.edges = defaultdict(set)
            level = context.objects.get(payload.nativePlanningLevelId)
            if not level or level["kind"] != "planningLevel":
                raise MetadataError("formula_level", "Choose an output planning level.")
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
                for node in walk((definition.get("formula") or {}).get("expression")):
                    if node["type"] == "measureRef":
                        context.edges[obj["objectId"]].add(node["measureId"])
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
