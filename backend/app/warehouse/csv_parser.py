import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import io
import re

from ..core.errors import MetadataError
from .schemas import WarehouseColumn


@dataclass(frozen=True)
class ParsedCsv:
    columns: list[WarehouseColumn]
    rows: list[list]


class CsvParser:
    """Parse original UTF-8 CSV on the server; infer without float precision loss."""

    NUMBER = re.compile(r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?$")
    DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def __init__(self, max_rows=5000, max_columns=200, max_cell_chars=65536):
        self.max_rows = max_rows
        self.max_columns = max_columns
        self.max_cell_chars = max_cell_chars

    def parse(self, content: bytes) -> ParsedCsv:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MetadataError(
                "invalid_encoding", "Save the CSV using UTF-8 encoding and try again."
            ) from exc
        if "\x00" in text:
            raise MetadataError(
                "invalid_csv", "The CSV contains null bytes; upload a UTF-8 CSV file."
            )
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            header = next((row for row in reader if row), None)
            if not header:
                raise MetadataError("missing_header", "The CSV must have a header row.")
            if len(header) > self.max_columns:
                raise MetadataError(
                    "column_limit",
                    f"CSV files may contain at most {self.max_columns} columns.",
                )
            if any(
                not name
                or name != name.strip()
                or len(name) > 128
                or any(ord(char) < 32 for char in name)
                for name in header
            ):
                raise MetadataError(
                    "invalid_header",
                    "Headers must be nonempty, at most 128 characters, and have no outer whitespace or control characters.",
                )
            if len(header) != len(set(header)):
                raise MetadataError(
                    "duplicate_header",
                    "CSV column headers must be unique; duplicate headers are not renamed automatically.",
                )
            rows = []
            for row in reader:
                if not row:
                    continue
                if len(rows) == self.max_rows:
                    raise MetadataError(
                        "row_limit",
                        f"CSV files may contain at most {self.max_rows:,} data rows, excluding the header.",
                    )
                if len(row) != len(header):
                    raise MetadataError(
                        "row_width",
                        f"CSV line {reader.line_num} has {len(row)} cells; expected {len(header)}.",
                    )
                if any(len(cell) > self.max_cell_chars for cell in row):
                    raise MetadataError(
                        "cell_limit",
                        f"CSV line {reader.line_num} contains a cell longer than {self.max_cell_chars:,} characters.",
                    )
                rows.append([None if cell == "" else cell for cell in row])
        except csv.Error as exc:
            raise MetadataError(
                "invalid_csv",
                f"The CSV is malformed near line {reader.line_num}; check quoting and field lengths.",
            ) from exc
        types = [
            self.infer([row[index] for row in rows if row[index] is not None])
            for index in range(len(header))
        ]
        columns = [
            WarehouseColumn(
                name=name,
                type=f"Nullable({dtype})"
                if not rows or any(row[index] is None for row in rows)
                else dtype,
            )
            for index, (name, dtype) in enumerate(zip(header, types))
        ]
        converted = [
            [self.convert(value, dtype) for value, dtype in zip(row, types)]
            for row in rows
        ]
        return ParsedCsv(columns=columns, rows=converted)

    @classmethod
    def infer(cls, values: list[str]) -> str:
        if not values:
            return "String"
        if all(value in {"true", "false"} for value in values):
            return "Bool"
        if all(cls.DATE.fullmatch(value) for value in values):
            try:
                dates = [date.fromisoformat(value) for value in values]
                if all(1900 <= value.year <= 2299 for value in dates):
                    return "Date32"
            except ValueError:
                pass
        if all(cls.NUMBER.fullmatch(value) for value in values):
            try:
                decimals = [Decimal(value) for value in values]
                scale = max(max(-value.as_tuple().exponent, 0) for value in decimals)
                if scale == 0 and all(
                    -(2**63) <= value <= 2**63 - 1 for value in decimals
                ):
                    return "Int64"
                integer_digits = max(max(value.adjusted() + 1, 0) for value in decimals)
                if scale <= 38 and integer_digits + scale <= 38:
                    return f"Decimal(38,{scale})"
            except (InvalidOperation, ValueError):
                pass
        # Mixed types, leading-zero identifiers, out-of-range numbers and ambiguous dates stay strings.
        return "String"

    @staticmethod
    def convert(value: str | None, dtype: str):
        if value is None:
            return None
        if dtype == "Int64":
            return int(value)
        if dtype.startswith("Decimal("):
            return Decimal(value)
        if dtype == "Bool":
            return value == "true"
        if dtype == "Date32":
            return date.fromisoformat(value)
        return value
