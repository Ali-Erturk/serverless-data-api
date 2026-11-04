import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from data_api.worker import LeaseBusy


@pytest.mark.parametrize("raw,fmt", [(b"a,b\n1,2\n", "csv"), (b'[{"a":1,"b":2}]', "json")])
def test_mocked_end_to_end(system, raw, fmt):
    dataset_id = system.create(raw, fmt)["dataset_id"]
    event = system.upload(dataset_id, raw)
    system.worker.handle(event)
    _, status = system.get(dataset_id)
    assert status["status"] == "completed"
    assert status["summary"]["row_count"] == 1
    item = system.store.get(dataset_id)
    obj = system.s3.get_object(Bucket=system.settings.results, Key=item["result_key"])
    result = json.loads(obj["Body"].read())
    assert result["summary"] == status["summary"]
    expected = [{"a": "1", "b": "2"}] if fmt == "csv" else [{"a": 1, "b": 2}]
    assert result["records"] == expected
    code, download = system.get(dataset_id, download=True)
    assert code == 200 and download["expires_in"] == 300
    assert (
        item["result_key"].replace("/", "%2F") in download["url"] or "/results/" in download["url"]
    )
    system.worker.handle(event)
    assert system.s3.list_objects_v2(Bucket=system.settings.results)["KeyCount"] == 1
    # Replaying a form writes a new version, but cannot replace a completed dataset.
    replay = system.upload(dataset_id, b"a,b\n9,9\n")
    system.worker.handle(replay)
    assert system.store.get(dataset_id)["result_key"] == item["result_key"]
    assert system.s3.list_objects_v2(Bucket=system.settings.results)["KeyCount"] == 1


@pytest.mark.parametrize(
    "raw,overrides,error",
    [
        (b"a,b\n1\n", {}, "RAGGED_CSV"),
        (b"a\n1\n", {"size_bytes": 10}, "SIZE_MISMATCH"),
        (b"a\n1\n", {"sha256": "0" * 64}, "CHECKSUM_MISMATCH"),
    ],
)
def test_validation_is_terminal(system, raw, overrides, error):
    dataset_id = system.create(raw, **overrides)["dataset_id"]
    event = system.upload(dataset_id, raw)
    system.worker.handle(event)
    system.worker.handle(event)
    _, status = system.get(dataset_id)
    assert status["status"] == "failed"
    assert status["error"]["code"] == error
    assert system.get(dataset_id, download=True)[0] == 409
    assert system.s3.list_objects_v2(Bucket=system.settings.results)["KeyCount"] == 0


def test_transient_storage_failure_is_retried(system, monkeypatch):
    dataset_id = system.create()["dataset_id"]
    event = system.upload(dataset_id)
    original = system.s3.put_object
    monkeypatch.setattr(
        system.s3,
        "put_object",
        Mock(
            side_effect=ClientError(
                {"Error": {"Code": "SlowDown", "Message": "retry"}}, "PutObject"
            )
        ),
    )
    with pytest.raises(ClientError):
        system.worker.handle(event)
    failed_attempt = system.store.get(dataset_id)
    assert failed_attempt["status"] == "processing" and failed_attempt["lease_until"] == 0
    monkeypatch.setattr(system.s3, "put_object", original)
    system.worker.handle(event)
    assert system.get(dataset_id)[1]["status"] == "completed"


def test_crashed_owner_lease_recovery_and_version_pinning(system):
    dataset_id = system.create()["dataset_id"]
    event = system.upload(dataset_id)
    version = event["Records"][0]["s3"]["object"]["versionId"]
    assert system.store.claim(dataset_id, version, "crashed-owner", system.clock[0], 60)
    with pytest.raises(LeaseBusy):
        system.worker.handle(event)
    # A later object version cannot take over the pinned job, even after the lease expires.
    replay = system.upload(dataset_id, b"a,b\n9,9\n")
    system.clock[0] += 61
    system.worker.handle(replay)
    assert system.store.get(dataset_id)["attempt_token"] == "crashed-owner"
    system.worker.handle(event)
    assert system.get(dataset_id)[1]["status"] == "completed"
    assert not system.store.finish(
        dataset_id,
        "crashed-owner",
        system.clock[0],
        {
            "status": "failed",
            "error_code": "STALE",
        },
    )
    assert system.get(dataset_id)[1]["status"] == "completed"


def test_only_one_claim_and_fenced_commit(system):
    dataset_id = system.create()["dataset_id"]
    now = system.clock[0]
    assert system.store.claim(dataset_id, "v1", "a", now, 60)
    assert system.store.claim(dataset_id, "v1", "b", now, 60) is None
    assert system.store.claim(dataset_id, "v1", "b", now + 61, 60)
    assert not system.store.finish(dataset_id, "a", now + 61, {"status": "completed"})
    assert system.store.finish(dataset_id, "b", now + 61, {"status": "completed"})


def test_expired_and_unknown_events_are_ignored(system):
    created = system.create()
    event = system.upload(created["dataset_id"])
    system.clock[0] = created["job_deadline"]
    system.worker.handle(event)
    assert system.get(created["dataset_id"])[1]["error"]["code"] == "JOB_EXPIRED"
    unknown = deepcopy(event)
    unknown["Records"][0]["s3"]["object"]["key"] = "uploads/unknown/input.csv"
    system.worker.handle(unknown)
    system.worker.handle({"Event": "s3:TestEvent"})


@pytest.mark.parametrize("mutation", ["bucket", "version", "source", "key"])
def test_unexpected_event_rejected(system, mutation):
    dataset_id = system.create()["dataset_id"]
    event = system.upload(dataset_id)
    record = event["Records"][0]
    if mutation == "bucket":
        record["s3"]["bucket"]["name"] = "untrusted"
    elif mutation == "version":
        del record["s3"]["object"]["versionId"]
    elif mutation == "source":
        record["eventSource"] = "other"
    else:
        record["s3"]["object"]["key"] = "bad/path"
    with pytest.raises(ValueError):
        system.worker.handle(event)
