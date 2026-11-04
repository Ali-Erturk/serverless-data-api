"""AWS-credential-chain client. Requires execute-api:Invoke, no direct S3 permissions."""

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError

from data_api.config import MAX_BYTES


class Client:
    def __init__(self, api_url, region, profile=None):
        parsed = urlparse(api_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("Provide an HTTPS API base URL without query or fragment")
        self.api_url = api_url.rstrip("/")
        self.region = region
        self.session = boto3.Session(profile_name=profile, region_name=region)

    def request(self, method, path, payload=None):
        credentials = self.session.get_credentials()
        if credentials is None:
            raise RuntimeError("No AWS credentials found; configure an AWS profile or SSO session")
        body = json.dumps(payload) if payload is not None else None
        signed = AWSRequest(
            method=method,
            url=self.api_url + path,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        SigV4Auth(credentials.get_frozen_credentials(), "execute-api", self.region).add_auth(signed)
        response = requests.request(
            method,
            signed.url,
            data=body,
            headers=dict(signed.headers),
            timeout=20,
            allow_redirects=False,
        )
        if response.status_code >= 400:
            # Responses from our API are safe codes; avoid printing URLs/headers.
            raise RuntimeError(f"API returned {response.status_code}: {response.text[:1000]}")
        return response.json()

    def check_unauthorized(self):
        unknown = str(uuid.uuid4())
        for method, path in [
            ("POST", "/uploads"),
            ("GET", f"/datasets/{unknown}"),
            ("GET", f"/datasets/{unknown}/download"),
        ]:
            response = requests.request(
                method, self.api_url + path, timeout=20, allow_redirects=False
            )
            if response.status_code != 403:
                raise RuntimeError(
                    f"Unsigned {method} {path} returned {response.status_code}, not 403"
                )
        print("All three routes rejected unsigned requests (403).")

    def run(self, file, output, timeout=180):
        path = Path(file)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("File exceeds 1 MiB")
        raw = path.read_bytes()
        fmt = path.suffix.lower().lstrip(".")
        if not raw or fmt not in {"csv", "json"}:
            raise ValueError("Supply a nonempty .csv or .json file")
        created = self.request(
            "POST",
            "/uploads",
            {
                "format": fmt,
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        dataset_id = created["dataset_id"]
        form = created["upload"]
        uploaded = requests.post(
            form["url"],
            data=form["fields"],
            files={"file": (path.name, raw, form["fields"]["Content-Type"])},
            timeout=30,
            allow_redirects=False,
        )
        if uploaded.status_code not in {200, 201, 204}:
            raise RuntimeError(f"S3 upload returned {uploaded.status_code}")
        print(f"Uploaded dataset {dataset_id}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.request("GET", f"/datasets/{dataset_id}")
            if status["status"] == "completed":
                download = self.request("GET", f"/datasets/{dataset_id}/download")
                result = requests.get(download["url"], timeout=30, allow_redirects=False)
                if result.status_code != 200:
                    raise RuntimeError(f"Result download returned {result.status_code}")
                parsed = result.json()
                if parsed["summary"] != status["summary"]:
                    raise RuntimeError("Downloaded summary differs from status response")
                Path(output).write_bytes(result.content)
                print(json.dumps({"dataset_id": dataset_id, "summary": parsed["summary"]}))
                return parsed
            if status["status"] == "failed":
                raise RuntimeError(f"Dataset failed: {status['error']['code']}")
            time.sleep(2)
        raise TimeoutError(f"Polling timed out; resume with GET /datasets/{dataset_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--file", default="samples/inventory.csv")
    parser.add_argument("--output", default="result.json")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--verify-unauthorized", action="store_true")
    args = parser.parse_args()
    try:
        client = Client(args.api_url, args.region, args.profile)
        if args.verify_unauthorized:
            client.check_unauthorized()
        client.run(args.file, args.output, args.timeout)
    except (requests.RequestException, BotoCoreError) as exc:
        # Exception strings can contain signed URLs. Report only a safe type.
        parser.exit(1, f"Network or credential failure: {type(exc).__name__}\n")
    except (RuntimeError, ValueError, TimeoutError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
