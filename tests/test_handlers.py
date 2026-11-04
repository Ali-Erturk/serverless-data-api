import hashlib
import json
from types import SimpleNamespace

from data_api import api, worker


def test_lambda_entrypoints_initialize_and_reuse_sdk_clients(system, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", system.settings.table)
    monkeypatch.setenv("UPLOAD_BUCKET", system.settings.uploads)
    monkeypatch.setenv("RESULT_BUCKET", system.settings.results)
    monkeypatch.setattr(api, "_app", None)
    monkeypatch.setattr(worker, "_worker", None)
    context = SimpleNamespace(aws_request_id="lambda-test")
    raw = b"a\n1\n"
    created = api.handler(
        {
            "routeKey": "POST /uploads",
            "body": json.dumps(
                {
                    "format": "csv",
                    "size_bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ),
        },
        context,
    )
    assert created["statusCode"] == 201
    created = json.loads(created["body"])
    assert created["upload"]["fields"]["x-amz-algorithm"] == "AWS4-HMAC-SHA256"
    assert ".s3.us-east-1.amazonaws.com" in created["upload"]["url"]
    dataset_id = created["dataset_id"]
    event = system.upload(dataset_id, raw)
    worker.handler(event, context)
    worker.handler(event, context)
    status = api.handler(
        {"routeKey": "GET /datasets/{id}", "pathParameters": {"id": dataset_id}}, context
    )
    assert json.loads(status["body"])["status"] == "completed"
