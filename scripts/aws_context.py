"""Read-only account/region inspection. Does not create AWS resources or print secrets."""

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def main():
    session = boto3.Session()
    region = session.region_name
    try:
        credentials = session.get_credentials()
        if credentials is None:
            print(json.dumps({"credentials_available": False, "region": region}))
            return
        identity = session.client("sts", region_name=region or "us-east-1").get_caller_identity()
        print(
            json.dumps(
                {
                    "credentials_available": True,
                    "region": region,
                    "account": identity["Account"],
                    "principal_arn": identity["Arn"],
                }
            )
        )
    except (BotoCoreError, ClientError) as exc:
        print(
            json.dumps(
                {"credentials_available": False, "region": region, "error_type": type(exc).__name__}
            )
        )


if __name__ == "__main__":
    main()
