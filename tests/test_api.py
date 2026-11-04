import base64
import json
import uuid

import pytest

from data_api.config import MAX_BYTES, UPLOAD_SECONDS


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        None,
        {"format": "csv", "size_bytes": 1, "sha256": "x" * 64},
        {"format": "xml", "size_bytes": 1, "sha256": "a" * 64},
        {"format": "csv", "size_bytes": 0, "sha256": "a" * 64},
        {"format": "csv", "size_bytes": MAX_BYTES + 1, "sha256": "a" * 64},
        {"format": "csv", "size_bytes": True, "sha256": "a" * 64},
        {"format": [], "size_bytes": 1, "sha256": "a" * 64},
        {"format": "csv", "size_bytes": 1, "sha256": "a" * 64, "key": "override"},
    ],
)
def test_create_validation(system, payload):
    result = system.api.handle({"routeKey": "POST /uploads", "body": json.dumps(payload)})
    assert result["statusCode"] == 400
    assert json.loads(result["body"])["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "body,encoded",
    [
        ("{", False),
        ("x" * 4097, False),
        ("!!", True),
        ("[" * 1500 + "]" * 1500, False),
    ],
)
def test_bad_body(system, body, encoded):
    assert (
        system.api.handle({"routeKey": "POST /uploads", "body": body, "isBase64Encoded": encoded})[
            "statusCode"
        ]
        == 400
    )


def test_upload_policy_and_private_status(system):
    created = system.create()
    dataset_id = created["dataset_id"]
    assert str(uuid.UUID(dataset_id)) == dataset_id
    policy = json.loads(base64.b64decode(created["upload"]["fields"]["policy"]))
    conditions = policy["conditions"]
    assert ["content-length-range", 8, 8] in conditions
    assert {"Content-Type": "text/csv"} in conditions
    assert {"key": f"uploads/{dataset_id}/input.csv"} in conditions
    assert created["upload_expires_at"] - system.clock[0] == UPLOAD_SECONDS
    code, status = system.get(dataset_id)
    assert code == 200 and status["status"] == "awaiting_upload"
    assert not {"sha256", "upload_key", "input_version", "attempt_token"} & status.keys()
    assert system.get(dataset_id, download=True)[0] == 409


def test_unknown_invalid_and_missing_ids(system):
    assert system.get(str(uuid.uuid4()))[0] == 404
    assert system.get("invalid")[0] == 400
    assert system.api.handle({"routeKey": "GET /datasets/{id}"})["statusCode"] == 400
    assert system.api.handle({"routeKey": "GET /nope"})["statusCode"] == 404


def test_abandoned_and_expired_dataset(system):
    created = system.create()
    system.clock[0] = created["job_deadline"]
    _, status = system.get(created["dataset_id"])
    assert status["status"] == "failed"
    assert status["error"]["code"] == "JOB_EXPIRED"
    item = system.store.get(created["dataset_id"])
    system.clock[0] = int(item["expires_at"])
    assert system.get(created["dataset_id"])[1]["error"]["code"] == "DATASET_EXPIRED"
    assert system.get(created["dataset_id"], download=True)[0] == 409


def test_exception_is_sanitized(system, monkeypatch, capsys):
    monkeypatch.setattr(
        system.store,
        "get",
        lambda _: (_ for _ in ()).throw(RuntimeError("sensitive data must not appear")),
    )
    code, body = system.get(str(uuid.uuid4()))
    assert code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "sensitive" not in capsys.readouterr().out


def test_base64_body(system):
    payload = json.dumps({"format": "json", "size_bytes": 2, "sha256": "a" * 64})
    result = system.api.handle(
        {
            "routeKey": "POST /uploads",
            "isBase64Encoded": True,
            "body": base64.b64encode(payload.encode()).decode(),
        }
    )
    assert result["statusCode"] == 201
