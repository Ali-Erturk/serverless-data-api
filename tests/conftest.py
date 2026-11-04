import hashlib
import json
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from data_api.api import API
from data_api.config import Settings
from data_api.store import Store
from data_api.worker import Worker


@pytest.fixture
def system(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_EC2_METADATA_DISABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        for bucket in ["test-uploads", "test-results"]:
            s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(
            Bucket="test-uploads", VersioningConfiguration={"Status": "Enabled"}
        )
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="datasets",
            KeySchema=[{"AttributeName": "dataset_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "dataset_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        settings = Settings("datasets", "test-uploads", "test-results")
        clock = [1_800_000_000]
        store = Store(table)
        api = API(store, s3, settings, clock=lambda: clock[0])
        worker = Worker(store, s3, settings, clock=lambda: clock[0])

        def create(raw=b"a,b\n1,2\n", fmt="csv", **overrides):
            payload = {
                "format": fmt,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            payload.update(overrides)
            result = api.handle({"routeKey": "POST /uploads", "body": json.dumps(payload)})
            assert result["statusCode"] == 201
            return json.loads(result["body"])

        def upload(dataset_id, raw=b"a,b\n1,2\n"):
            item = store.get(dataset_id)
            uploaded = s3.put_object(Bucket=settings.uploads, Key=item["upload_key"], Body=raw)
            return {
                "Records": [
                    {
                        "eventSource": "aws:s3",
                        "eventName": "ObjectCreated:Post",
                        "s3": {
                            "bucket": {"name": settings.uploads},
                            "object": {
                                "key": item["upload_key"],
                                "versionId": uploaded["VersionId"],
                            },
                        },
                    }
                ]
            }

        def get(dataset_id, download=False):
            route = "GET /datasets/{id}" + ("/download" if download else "")
            result = api.handle({"routeKey": route, "pathParameters": {"id": dataset_id}})
            return result["statusCode"], json.loads(result["body"])

        yield SimpleNamespace(
            s3=s3,
            store=store,
            api=api,
            worker=worker,
            settings=settings,
            clock=clock,
            create=create,
            upload=upload,
            get=get,
        )
