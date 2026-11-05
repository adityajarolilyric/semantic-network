"""Resolve user-facing symbols to stable semantic identities, never SQL names."""

from ..core.errors import MetadataError
from ..domain.formula_attributes import formula_attributes
from .catalog import BY_NAME


class FormulaResolver:
    def __init__(self, objects, native_level):
        self.objects = objects
        self.native = native_level
        self.attributes = {}

    @staticmethod
    def error(node, message):
        raise MetadataError(
            "formula_reference",
            message,
            details=[
                {
                    "line": node.get("line", 1),
                    "column": node.get("column", 1),
                    "message": message,
                }
            ],
        )

    def resolve(self, node, inside_count=False, depth=0):
        if depth > 32:
            raise MetadataError("formula_depth", "Formula nesting exceeds 32 levels.")
        kind = node["type"]
        if kind == "literal":
            return node
        if kind == "symbol":
            matches = [
                obj
                for obj in self.objects
                if obj["kind"] == "measure"
                and (obj["name"] == node["name"] or obj["objectId"] == node["name"])
            ]
            level = self.native
            if "." in node["name"] and level not in self.attributes:
                self.attributes[level] = formula_attributes(self.objects, level)
            attributes = [
                item
                for item in self.attributes.get(level, [])
                if node["name"]
                in {item["label"], item["key"], *item.get("aliases", [])}
            ]
            if len(matches) + len(attributes) != 1:
                self.error(
                    node,
                    "Choose a unique measure or an available [TableName.ColumnName] reference: "
                    + node["name"],
                )
            if attributes:
                return attributes[0]["reference"]
            return {"type": "measureRef", "measureId": matches[0]["objectId"]}
        if kind == "binary":
            return {
                **node,
                "left": self.resolve(node["left"], inside_count, depth + 1),
                "right": self.resolve(node["right"], inside_count, depth + 1),
            }
        name = node["name"]
        spec = BY_NAME.get(name)
        if not spec:
            self.error(node, "Unsupported function " + name.upper())
        args = node["arguments"]
        if not spec.minimum <= len(args) <= spec.maximum:
            self.error(node, "Invalid argument count for " + name.upper())
        if spec.category == "aggregate":
            if inside_count:
                self.error(
                    node,
                    "Nested counts are not supported. Combine separate count expressions instead.",
                )
            return {
                "type": "count",
                "function": name,
                "arguments": [self.resolve(arg, True, depth + 1) for arg in args],
            }
        if spec.category == "reference":
            if args[0]["type"] not in {"symbol", "literal"} or not isinstance(
                args[0].get("name", args[0].get("value")), str
            ):
                self.error(node, "Choose an attribute or grain column name.")
            label = args[0].get("name", args[0].get("value"))
            level = self.native
            if name == "grain":
                owner = next(
                    (
                        obj
                        for obj in self.objects
                        if obj["objectId"] == level and obj["kind"] == "planningLevel"
                    ),
                    None,
                )
                matches = (
                    [
                        column
                        for column in owner["definition"]["grain"]["columns"]
                        if label in {column["name"], column["grainColumnId"]}
                    ]
                    if owner
                    else []
                )
                if len(matches) != 1:
                    self.error(node, "Choose an available, unique grain column.")
                if inside_count:
                    return {
                        "type": "attributeRef",
                        "planningLevelId": level,
                        "columnId": matches[0]["grainColumnId"],
                    }
                return {
                    "type": "grainRef",
                    "grainColumnId": matches[0]["grainColumnId"],
                }
            if level not in self.attributes:
                self.attributes[level] = formula_attributes(self.objects, level)
            matches = [
                item
                for item in self.attributes[level]
                if label in {item["label"], item["key"], *item.get("aliases", [])}
            ]
            if len(matches) != 1:
                self.error(
                    node,
                    "Choose an attribute available at the evaluation level: " + label,
                )
            return matches[0]["reference"]
        return {
            "type": "call",
            "function": name,
            "arguments": [self.resolve(arg, inside_count, depth + 1) for arg in args],
        }
