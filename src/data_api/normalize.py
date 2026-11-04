import csv
import io
import json
import math
import time

from data_api.config import (
    MAX_BYTES,
    MAX_CELL_BYTES,
    MAX_COLUMN_BYTES,
    MAX_COLUMNS,
    MAX_RESULT_BYTES,
    MAX_ROWS,
    PARSE_SECONDS,
)


class InvalidDataset(ValueError):
    """A safe, fixed validation code suitable for returning to the caller."""


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise InvalidDataset("DUPLICATE_JSON_KEY")
        obj[key] = value
    return obj


def _constant(_):
    raise InvalidDataset("NONFINITE_NUMBER")


def _utf8(value):
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidDataset("MALFORMED_INPUT") from None


def normalize(raw: bytes, file_format: str):
    if not raw:
        raise InvalidDataset("EMPTY_INPUT")
    if len(raw) > MAX_BYTES:
        raise InvalidDataset("UPLOAD_TOO_LARGE")
    deadline = time.monotonic() + PARSE_SECONDS
    try:
        text = raw.decode("utf-8-sig")
        if file_format == "csv":
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            columns = next(reader, [])
            records = []
            for row in reader:
                if len(row) != len(columns):
                    raise InvalidDataset("RAGGED_CSV")
                records.append(dict(zip(columns, row, strict=True)))
                if len(records) > MAX_ROWS:
                    raise InvalidDataset("TOO_MANY_ROWS")
        elif file_format == "json":
            records = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
            if not isinstance(records, list):
                raise InvalidDataset("EXPECTED_RECORD_ARRAY")
            columns = list(records[0]) if records and isinstance(records[0], dict) else []
        else:
            raise InvalidDataset("UNSUPPORTED_FORMAT")
    except InvalidDataset:
        raise
    except (UnicodeDecodeError, csv.Error, ValueError, RecursionError, StopIteration):
        raise InvalidDataset("MALFORMED_INPUT") from None

    if not records:
        raise InvalidDataset("EMPTY_INPUT")
    if len(records) > MAX_ROWS:
        raise InvalidDataset("TOO_MANY_ROWS")
    if not columns or len(columns) > MAX_COLUMNS:
        raise InvalidDataset("INVALID_COLUMNS")
    if len(set(columns)) != len(columns):
        raise InvalidDataset("DUPLICATE_COLUMNS")
    for col in columns:
        if not col.strip() or len(_utf8(col)) > MAX_COLUMN_BYTES or "\x00" in col:
            raise InvalidDataset("INVALID_COLUMNS")
    column_set = set(columns)
    normalized = []
    for record in records:
        if time.monotonic() > deadline:
            raise InvalidDataset("PROCESSING_LIMIT")
        if not isinstance(record, dict) or set(record) != column_set:
            raise InvalidDataset("INCONSISTENT_COLUMNS")
        output = {}
        for col in columns:
            value = record[col]
            if isinstance(value, (dict, list)):
                raise InvalidDataset("NESTED_VALUE")
            if isinstance(value, float) and not math.isfinite(value):
                raise InvalidDataset("NONFINITE_NUMBER")
            if isinstance(value, str) and ("\x00" in value or len(_utf8(value)) > MAX_CELL_BYTES):
                raise InvalidDataset("INVALID_CELL")
            output[col] = value
        normalized.append(output)
    summary = {"row_count": len(normalized), "columns": columns, "input_bytes": len(raw)}
    try:
        result = json.dumps(
            {"schema_version": 1, "summary": summary, "records": normalized},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (UnicodeEncodeError, ValueError):
        raise InvalidDataset("MALFORMED_INPUT") from None
    if len(result) > MAX_RESULT_BYTES:
        raise InvalidDataset("RESULT_TOO_LARGE")
    return result, summary
