import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.credentials import Credentials

from data_api import client as module
from data_api.client import Client


def make_client(monkeypatch):
    session = SimpleNamespace(get_credentials=lambda: Credentials("testing", "testing", "token"))
    monkeypatch.setattr(module.boto3, "Session", lambda **_: session)
    return Client("https://example.execute-api.us-east-1.amazonaws.com", "us-east-1")


def test_client_signs_request_with_session_token(monkeypatch):
    client = make_client(monkeypatch)
    transport = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {"ok": True}))
    monkeypatch.setattr(module.requests, "request", transport)
    assert client.request("POST", "/uploads", {"format": "csv"}) == {"ok": True}
    headers = transport.call_args.kwargs["headers"]
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "/us-east-1/execute-api/aws4_request" in headers["Authorization"]
    assert headers["X-Amz-Security-Token"] == "token"
    assert transport.call_args.kwargs["allow_redirects"] is False


def test_unsigned_checks_all_routes_and_rejects_unexpected_success(monkeypatch):
    client = make_client(monkeypatch)
    transport = Mock(return_value=SimpleNamespace(status_code=403))
    monkeypatch.setattr(module.requests, "request", transport)
    client.check_unauthorized()
    assert transport.call_count == 3
    assert all("headers" not in call.kwargs for call in transport.call_args_list)
    transport.return_value = SimpleNamespace(status_code=200)
    with pytest.raises(RuntimeError, match="not 403"):
        client.check_unauthorized()


def test_client_upload_poll_and_download(monkeypatch, tmp_path):
    client = make_client(monkeypatch)
    file = tmp_path / "sample.csv"
    file.write_bytes(b"a\n1\n")
    result = {
        "summary": {"row_count": 1, "columns": ["a"], "input_bytes": 4},
        "records": [{"a": "1"}],
        "schema_version": 1,
    }
    request = Mock(
        side_effect=[
            {
                "dataset_id": "id",
                "upload": {
                    "url": "https://s3.example/upload",
                    "fields": {"Content-Type": "text/csv", "key": "uploads/id/input.csv"},
                },
            },
            {"status": "processing"},
            {"status": "completed", "summary": result["summary"]},
            {"url": "https://s3.example/download?temporary=secret"},
        ]
    )
    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(module.requests, "post", lambda *a, **k: SimpleNamespace(status_code=204))
    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *a, **k: SimpleNamespace(
            status_code=200, json=lambda: result, content=json.dumps(result).encode()
        ),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    output = tmp_path / "output.json"
    assert client.run(file, output) == result
    assert json.loads(output.read_bytes()) == result
    assert request.call_args_list[0].args[2]["size_bytes"] == 4
    assert len(request.call_args_list[0].args[2]["sha256"]) == 64


def test_client_reports_failed_job_without_attempting_download(monkeypatch, tmp_path):
    client = make_client(monkeypatch)
    file = tmp_path / "sample.csv"
    file.write_bytes(b"a\n1\n")
    monkeypatch.setattr(
        client,
        "request",
        Mock(
            side_effect=[
                {
                    "dataset_id": "id",
                    "upload": {"url": "https://s3.example", "fields": {"Content-Type": "text/csv"}},
                },
                {"status": "failed", "error": {"code": "RAGGED_CSV"}},
            ]
        ),
    )
    monkeypatch.setattr(module.requests, "post", lambda *a, **k: SimpleNamespace(status_code=204))
    with pytest.raises(RuntimeError, match="RAGGED_CSV"):
        client.run(file, tmp_path / "output.json")


def test_client_rejects_plaintext_endpoint():
    with pytest.raises(ValueError, match="HTTPS"):
        Client("http://example.com", "us-east-1")
