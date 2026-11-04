import hashlib
import time
import uuid
from urllib.parse import unquote_plus

import boto3

from data_api.config import LEASE_SECONDS, MAX_BYTES, Settings
from data_api.logging import log
from data_api.normalize import InvalidDataset, normalize
from data_api.store import Store


class LeaseBusy(RuntimeError):
    pass


class Worker:
    def __init__(self, store, s3, settings, clock=time.time):
        self.store, self.s3, self.settings, self.clock = store, s3, settings, clock

    def handle(self, event, context=None):
        request_id = getattr(context, "aws_request_id", "local")
        if event.get("Event") == "s3:TestEvent":
            return
        for record in event["Records"]:
            self.process(record, request_id)

    def process(self, record, request_id):
        if record.get("eventSource") != "aws:s3" or not record["eventName"].startswith(
            "ObjectCreated:"
        ):
            raise ValueError("Unexpected event source")
        source = record["s3"]
        key = unquote_plus(source["object"]["key"])
        if source["bucket"]["name"] != self.settings.uploads:
            raise ValueError("Unexpected bucket")
        parts = key.split("/")
        if len(parts) != 3 or parts[0] != "uploads":
            raise ValueError("Unexpected key")
        dataset_id = parts[1]
        item = self.store.get(dataset_id)
        if not item or item["upload_key"] != key:
            log("unknown_upload_ignored", request_id=request_id)
            return
        version = source["object"].get("versionId")
        if not version or version == "null":
            raise ValueError("Versioned uploads required")
        token = str(uuid.uuid4())
        now = int(self.clock())
        claimed = self.store.claim(dataset_id, version, token, now, LEASE_SECONDS)
        if not claimed:
            current = self.store.get(dataset_id)
            if (
                current
                and current["status"] == "processing"
                and current.get("input_version") == version
                and int(current["job_deadline"]) > now
            ):
                # Retry instead of acknowledging an event whose active owner could crash.
                raise LeaseBusy("Processing lease is active")
            log("duplicate_or_expired_ignored", dataset_id=dataset_id, request_id=request_id)
            return
        log("processing_started", dataset_id=dataset_id, request_id=request_id)
        try:
            obj = self.s3.get_object(Bucket=self.settings.uploads, Key=key, VersionId=version)
            try:
                raw = obj["Body"].read(MAX_BYTES + 1)
            finally:
                obj["Body"].close()
            if len(raw) > MAX_BYTES:
                raise InvalidDataset("UPLOAD_TOO_LARGE")
            if len(raw) != int(claimed["size_bytes"]):
                raise InvalidDataset("SIZE_MISMATCH")
            if hashlib.sha256(raw).hexdigest() != claimed["sha256"]:
                raise InvalidDataset("CHECKSUM_MISMATCH")
            result, summary = normalize(raw, claimed["format"])
            # Each attempt writes a different key; only the fenced winning attempt is exposed.
            result_key = f"results/{dataset_id}/{token}.json"
            self.s3.put_object(
                Bucket=self.settings.results,
                Key=result_key,
                Body=result,
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )
            won = self.store.finish(
                dataset_id,
                token,
                int(self.clock()),
                {
                    "status": "completed",
                    "result_key": result_key,
                    "summary": summary,
                },
            )
            log(
                "processing_completed" if won else "stale_attempt_ignored",
                dataset_id=dataset_id,
                request_id=request_id,
            )
        except InvalidDataset as exc:
            self.store.finish(
                dataset_id,
                token,
                int(self.clock()),
                {
                    "status": "failed",
                    "error_code": str(exc),
                },
            )
            log(
                "validation_failed",
                dataset_id=dataset_id,
                request_id=request_id,
                error_code=str(exc),
            )
        except Exception as exc:
            self.store.release(dataset_id, token, int(self.clock()))
            log(
                "processing_retry",
                dataset_id=dataset_id,
                request_id=request_id,
                exception_type=type(exc).__name__,
            )
            raise


_worker = None


def handler(event, context):
    global _worker
    if _worker is None:
        settings = Settings.from_env()
        _worker = Worker(
            Store(boto3.resource("dynamodb").Table(settings.table)), boto3.client("s3"), settings
        )
    return _worker.handle(event, context)
