from botocore.exceptions import ClientError


def conditional_failed(exc):
    return isinstance(exc, ClientError) and (
        exc.response["Error"]["Code"] == "ConditionalCheckFailedException"
    )


class Store:
    def __init__(self, table):
        self.table = table

    def get(self, dataset_id):
        return self.table.get_item(Key={"dataset_id": dataset_id}, ConsistentRead=True).get("Item")

    def create(self, item):
        self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(dataset_id)")

    def claim(self, dataset_id, version, token, now, lease_seconds):
        try:
            return self.table.update_item(
                Key={"dataset_id": dataset_id},
                UpdateExpression=(
                    "SET #s = :processing, input_version = :v, lease_until = :lease, "
                    "attempt_token = :token, updated_at = :now"
                ),
                ConditionExpression=(
                    "attribute_exists(dataset_id) AND job_deadline > :now AND "
                    "(attribute_not_exists(input_version) OR input_version = :v) AND "
                    "(#s = :awaiting OR (#s = :processing AND lease_until <= :now))"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":processing": "processing",
                    ":awaiting": "awaiting_upload",
                    ":v": version,
                    ":token": token,
                    ":now": now,
                    ":lease": now + lease_seconds,
                },
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except ClientError as exc:
            if conditional_failed(exc):
                return None
            raise

    def finish(self, dataset_id, token, now, fields):
        names = {"#s": "status"}
        values = {":token": token, ":processing": "processing", ":now": now}
        updates = ["updated_at = :now"]
        for i, (key, value) in enumerate(fields.items()):
            names[f"#f{i}"] = key
            values[f":f{i}"] = value
            updates.append(f"#f{i} = :f{i}")
        try:
            self.table.update_item(
                Key={"dataset_id": dataset_id},
                UpdateExpression="SET " + ", ".join(updates),
                ConditionExpression="attempt_token = :token AND #s = :processing",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if conditional_failed(exc):
                return False
            raise

    def release(self, dataset_id, token, now):
        # Preserve input version and state. A retry may reclaim this attempt immediately.
        return self.finish(dataset_id, token, now, {"lease_until": 0})
