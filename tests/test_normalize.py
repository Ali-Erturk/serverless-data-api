import json

import pytest

from data_api import normalize as module
from data_api.config import MAX_BYTES, MAX_COLUMNS, MAX_ROWS
from data_api.normalize import InvalidDataset, normalize


@pytest.mark.parametrize(
    ("raw", "fmt", "code"),
    [
        (b"", "csv", "EMPTY_INPUT"),
        (b"a,b\n", "csv", "EMPTY_INPUT"),
        (b"[]", "json", "EMPTY_INPUT"),
        (b"a,b\n1\n", "csv", "RAGGED_CSV"),
        (b"a,b\n1,2,3\n", "csv", "RAGGED_CSV"),
        (b'a\n"unterminated', "csv", "MALFORMED_INPUT"),
        (b"a,a\n1,2\n", "csv", "DUPLICATE_COLUMNS"),
        (b" ,b\n1,2\n", "csv", "INVALID_COLUMNS"),
        (b"\xff\xfe", "csv", "MALFORMED_INPUT"),
        (b"{", "json", "MALFORMED_INPUT"),
        (b"{}", "json", "EXPECTED_RECORD_ARRAY"),
        (b'[{"a":1,"a":2}]', "json", "DUPLICATE_JSON_KEY"),
        (b'[{"a":[]}]', "json", "NESTED_VALUE"),
        (b'[{"a":{} }]', "json", "NESTED_VALUE"),
        (b'[{"a":1},{"b":2}]', "json", "INCONSISTENT_COLUMNS"),
        (b'[{"a":1},2]', "json", "INCONSISTENT_COLUMNS"),
        (b'[{"a":NaN}]', "json", "NONFINITE_NUMBER"),
        (b'[{"a":1e999}]', "json", "NONFINITE_NUMBER"),
        (b'[{"a":"\\u0000"}]', "json", "INVALID_CELL"),
        (b'[{"a":"\\ud800"}]', "json", "MALFORMED_INPUT"),
        (b'[{"\\ud800":1}]', "json", "MALFORMED_INPUT"),
        (b'[{"a":' + b"1" * 5000 + b"}]", "json", "MALFORMED_INPUT"),
        (b"x", "xml", "UNSUPPORTED_FORMAT"),
        (b"x" * (MAX_BYTES + 1), "csv", "UPLOAD_TOO_LARGE"),
        (json.dumps([{"a": "x" * 4097}]).encode(), "json", "INVALID_CELL"),
        (json.dumps([{"x" * 129: 1}]).encode(), "json", "INVALID_COLUMNS"),
        (
            json.dumps([{str(i): i for i in range(MAX_COLUMNS + 1)}]).encode(),
            "json",
            "INVALID_COLUMNS",
        ),
        (json.dumps([{"a": 1}] * (MAX_ROWS + 1)).encode(), "json", "TOO_MANY_ROWS"),
        (b"a\n" + b"1\n" * (MAX_ROWS + 1), "csv", "TOO_MANY_ROWS"),
    ],
)
def test_rejects_invalid_data(raw, fmt, code):
    with pytest.raises(InvalidDataset, match=f"^{code}$"):
        normalize(raw, fmt)


def test_csv_preserves_strings_quotes_unicode_and_crlf():
    raw = '\ufeffsku,name\r\n001,"hello, dünyâ"\r\n002,"two\nlines"\r\n'.encode()
    result, summary = normalize(raw, "csv")
    assert json.loads(result)["records"] == [
        {"sku": "001", "name": "hello, dünyâ"},
        {"sku": "002", "name": "two\nlines"},
    ]
    assert summary == {"row_count": 2, "columns": ["sku", "name"], "input_bytes": len(raw)}


def test_json_keeps_scalar_types_and_first_record_column_order():
    raw = b'[{"b":true,"a":null},{"a":3.5,"b":false}]'
    result, summary = normalize(raw, "json")
    data = json.loads(result)
    assert data["schema_version"] == 1
    assert summary["columns"] == ["b", "a"]
    assert data["records"] == [{"b": True, "a": None}, {"b": False, "a": 3.5}]


def test_exact_row_limit_is_accepted():
    _, summary = normalize(b"a\n" + b"1\n" * MAX_ROWS, "csv")
    assert summary["row_count"] == MAX_ROWS


def test_processing_time_and_result_limits(monkeypatch):
    timer = iter([0, 11])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(timer))
    with pytest.raises(InvalidDataset, match="PROCESSING_LIMIT"):
        normalize(b"a\n1\n", "csv")
    monkeypatch.setattr(module.time, "monotonic", lambda: 0)
    monkeypatch.setattr(module, "MAX_RESULT_BYTES", 1)
    with pytest.raises(InvalidDataset, match="RESULT_TOO_LARGE"):
        normalize(b"a\n1\n", "csv")
