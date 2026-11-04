import base64
import json
import re
import time
import uuid
from decimal import Decimal

import boto3
from botocore.config import Config

from data_api.config import (
    CONTENT_TYPES,
    DOWNLOAD_SECONDS,
    JOB_SECONDS,
    MAX_BYTES,
    RETENTION_SECONDS,
    UPLOAD_SECONDS,
    Settings,
)
from data_api.logging import log
from data_api.store import Store


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value)
    raise TypeError(type(value).__name__)


def response(status, body, request_id):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps({**body, "request_id": request_id}, default=_json_default),
    }


def effective_error(item, now):
    if int(item["expires_at"]) <= now:
        return "DATASET_EXPIRED"
    if item["status"] in {"awaiting_upload", "processing"} and int(item["job_deadline"]) <= now:
        return "JOB_EXPIRED"
    return item.get("error_code")


class API:
    def __init__(self, store, s3, settings, clock=time.time):
        self.store, self.s3, self.settings, self.clock = store, s3, settings, clock

    def handle(self, event, context=None):
        request_id = event.get("requestContext", {}).get("requestId", "local")
        try:
            route = event.get("routeKey", "")
            if route == "POST /uploads":
                return self.create_upload(event, request_id)
            if route in {"GET /datasets/{id}", "GET /datasets/{id}/download"}:
                return self.get_dataset(event, request_id, route.endswith("/download"))
            return response(404, {"error": {"code": "ROUTE_NOT_FOUND"}}, request_id)
        except Exception as exc:
            log("api_error", request_id=request_id, exception_type=type(exc).__name__)
            return response(500, {"error": {"code": "INTERNAL_ERROR"}}, request_id)

    def create_upload(self, event, request_id):
        try:
            body = event.get("body") or ""
            if len(body) > 4096:
                raise ValueError
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body, validate=True).decode("utf-8")
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"format", "size_bytes", "sha256"}:
                raise ValueError
            fmt, size, digest = payload["format"], payload["size_bytes"], payload["sha256"]
            if not isinstance(fmt, str) or fmt not in CONTENT_TYPES:
                raise ValueError
            if type(size) is not int or not 1 <= size <= MAX_BYTES:
                raise ValueError
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError
        except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
            return response(400, {"error": {"code": "INVALID_REQUEST"}}, request_id)
        dataset_id = str(uuid.uuid4())
        now = int(self.clock())
        key = f"uploads/{dataset_id}/input.{fmt}"
        item = {
            "dataset_id": dataset_id,
            "status": "awaiting_upload",
            "format": fmt,
            "size_bytes": size,
            "sha256": digest,
            "upload_key": key,
            "created_at": now,
            "updated_at": now,
            "upload_expires_at": now + UPLOAD_SECONDS,
            "job_deadline": now + JOB_SECONDS,
            "expires_at": now + RETENTION_SECONDS,
        }
        upload = self.s3.generate_presigned_post(
            Bucket=self.settings.uploads,
            Key=key,
            Fields={"Content-Type": CONTENT_TYPES[fmt], "x-amz-meta-sha256": digest},
            Conditions=[
                {"Content-Type": CONTENT_TYPES[fmt]},
                {"x-amz-meta-sha256": digest},
                ["content-length-range", size, size],
            ],
            ExpiresIn=UPLOAD_SECONDS,
        )
        self.store.create(item)
        log("upload_created", dataset_id=dataset_id, request_id=request_id)
        return response(
            201,
            {
                "dataset_id": dataset_id,
                "status": "awaiting_upload",
                "upload": upload,
                "upload_expires_at": item["upload_expires_at"],
                "job_deadline": item["job_deadline"],
            },
            request_id,
        )

    def get_dataset(self, event, request_id, download):
        dataset_id = (event.get("pathParameters") or {}).get("id", "")
        try:
            if str(uuid.UUID(dataset_id)) != dataset_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            return response(400, {"error": {"code": "INVALID_DATASET_ID"}}, request_id)
        item = self.store.get(dataset_id)
        if not item:
            return response(404, {"error": {"code": "DATASET_NOT_FOUND"}}, request_id)
        error = effective_error(item, int(self.clock()))
        status = "failed" if error else item["status"]
        if download:
            if status != "completed":
                return response(
                    409, {"error": {"code": "RESULT_UNAVAILABLE"}, "status": status}, request_id
                )
            url = self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.settings.results, "Key": item["result_key"]},
                ExpiresIn=DOWNLOAD_SECONDS,
            )
            return response(200, {"url": url, "expires_in": DOWNLOAD_SECONDS}, request_id)
        public = {
            k: item[k]
            for k in (
                "dataset_id",
                "format",
                "created_at",
                "updated_at",
                "upload_expires_at",
                "job_deadline",
                "expires_at",
                "summary",
            )
            if k in item
        }
        public["status"] = status
        if error:
            public["error"] = {"code": error}
        return response(200, public, request_id)


_app = None


def handler(event, context):
    global _app
    if _app is None:
        settings = Settings.from_env()
        _app = API(
            Store(boto3.resource("dynamodb").Table(settings.table)),
            boto3.client(
                "s3",
                config=Config(
                    signature_version="s3v4",
                    s3={
                        "addressing_style": "virtual",
                        "us_east_1_regional_endpoint": "regional",
                    },
                ),
            ),
            settings,
        )
    return _app.handle(event, context)
