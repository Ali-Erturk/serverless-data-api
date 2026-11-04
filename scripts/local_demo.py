"""Demonstrate the complete application path using in-memory Moto services, never real AWS."""

import hashlib
import json
import os
from pathlib import Path

import boto3
from moto import mock_aws

from data_api.api import API
from data_api.config import Settings
from data_api.store import Store
from data_api.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


def main():
    print("OFFLINE DEMO: Moto mocks; no deployed AWS or authentication verification.")
    os.environ.update(
        {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        settings = Settings("demo-datasets", "demo-uploads", "demo-results")
        for bucket in [settings.uploads, settings.results]:
            s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(
            Bucket=settings.uploads, VersioningConfiguration={"Status": "Enabled"}
        )
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName=settings.table,
            KeySchema=[{"AttributeName": "dataset_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "dataset_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        store = Store(table)
        api, worker = API(store, s3, settings), Worker(store, s3, settings)
        cases = [
            ("inventory.csv", (ROOT / "samples/inventory.csv").read_bytes(), "csv"),
            ("inventory.json", (ROOT / "samples/inventory.json").read_bytes(), "json"),
            ("malformed.csv", b"a,b\n1\n", "csv"),
        ]
        for name, raw, fmt in cases:
            created = api.handle(
                {
                    "routeKey": "POST /uploads",
                    "body": json.dumps(
                        {
                            "format": fmt,
                            "size_bytes": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    ),
                }
            )
            assert created["statusCode"] == 201
            dataset_id = json.loads(created["body"])["dataset_id"]
            item = store.get(dataset_id)
            uploaded = s3.put_object(Bucket=settings.uploads, Key=item["upload_key"], Body=raw)
            event = {
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
            worker.handle(event)
            count_before = s3.list_objects_v2(Bucket=settings.results)["KeyCount"]
            worker.handle(event)
            assert s3.list_objects_v2(Bucket=settings.results)["KeyCount"] == count_before
            status = json.loads(
                api.handle(
                    {"routeKey": "GET /datasets/{id}", "pathParameters": {"id": dataset_id}}
                )["body"]
            )
            if name == "malformed.csv":
                assert status["status"] == "failed" and status["error"]["code"] == "RAGGED_CSV"
                print(f"{name}: rejected with RAGGED_CSV; duplicate delivery harmless")
            else:
                assert status["status"] == "completed" and status["summary"]["row_count"] == 3
                item = store.get(dataset_id)
                result = json.loads(
                    s3.get_object(Bucket=settings.results, Key=item["result_key"])["Body"].read()
                )
                assert result["summary"] == status["summary"]
                print(f"{name}: {json.dumps(result)}; duplicate delivery harmless")


if __name__ == "__main__":
    main()
