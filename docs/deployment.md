# AWS deployment

## Requirements

- Python 3.12, Terraform 1.14.9, and the AWS CLI.
- An AWS account and credentials with permission to manage this stack's services and pass its
  two Lambda execution roles. Temporary credentials or an SSO profile are preferred.
- A built `build/lambda.zip` and the checked-in dependency/provider locks.

The default configuration uses `us-east-1` and namespace `ali-data-api-demo`. Names, region, and
environment are configurable. `aws_account_id` is required; the provider rejects other accounts.

## Resources

Terraform manages two Python Lambda functions, an HTTP API with three IAM routes, private upload and
result buckets, an on-demand DynamoDB table, an encrypted SQS failure destination, scoped IAM roles
and a caller policy, three log groups, one log metric filter, and seven CloudWatch alarms.

Uploads use S3 versioning. Current inputs expire after one day, noncurrent versions after one day
as noncurrent, and results after seven days. Metadata uses seven-day application retention and TTL.
Lifecycle and TTL cleanup are asynchronous. Log retention is 14 days.

## Configure and deploy

From the repository root with the development environment activated:

```powershell
aws sso login --profile portfolio
$env:AWS_PROFILE = 'portfolio'
aws sts get-caller-identity
python scripts/build_lambda.py
Copy-Item infra/terraform.tfvars.example infra/terraform.tfvars
# Edit terraform.tfvars: set your actual account ID, region, and unique namespace.
terraform -chdir=infra init -input=false -lockfile=readonly
terraform -chdir=infra plan -out=deployment.tfplan
# Review the destination account and every planned resource before applying.
terraform -chdir=infra apply deployment.tfplan
```

State, plan files, variable files, and credentials are excluded from Git. Keep a secure state backup
until teardown succeeds. For shared or long-lived environments, configure encrypted remote state
and locking before deployment.

Terraform outputs `api_url`, `client_policy_arn`, bucket names, table name, worker name, and failure
queue URL. Attach the scoped caller policy to the intended caller role, or use an identity already
authorized to invoke the API. The caller does not require direct bucket or table permissions.

## Upload, process, and download

```powershell
$apiUrl = terraform -chdir=infra output -raw api_url
data-api --api-url $apiUrl --region us-east-1 --profile portfolio --file samples/inventory.csv --verify-unauthorized
Get-Content result.json
data-api --api-url $apiUrl --region us-east-1 --profile portfolio --file samples/inventory.json
```

Use the configured region if different from the default. The client checks unsigned requests on all
three routes for `403`, signs API requests, uploads using the returned form, polls processing state,
downloads the result, and compares its summary with the status response.

Service integration checks should also cover malformed-file failure, unknown-ID `404`, pending-result
`409`, duplicate event delivery using the original object version, and S3 rejection of oversized or
tampered forms. Local tests use service models; these checks exercise AWS service enforcement.

## Observe and recover

Structured logs contain request/dataset IDs and fixed event codes, excluding raw contents, credentials,
and signed URLs. Alarms cover Lambda errors, worker throttles, dropped events, destination delivery
failures, API internal errors, and queued invocation failures. Supply existing SNS topic ARNs in
`alarm_actions` for notifications; otherwise inspect alarms in CloudWatch.

For a failed asynchronous invocation, inspect its record in the failure queue. The original S3 event
is under `requestPayload`. Fix the transient cause, wait for any processing lease to expire, and
invoke the worker with that original payload and version ID before the job deadline. After the
deadline, create a new upload. Completed jobs should not be edited to retry processing.

## Costs

API requests, Lambda requests/compute, S3 requests/storage/versions, DynamoDB requests/storage, SQS,
CloudWatch logs/metrics/alarms, and outbound transfer can incur charges. Idle alarms and logs can
still contribute to a bill. Use the [AWS Pricing Calculator](https://calculator.aws/) for the selected
region and expected usage. API throttling is not a spending cap. Tear down unused environments.

## Teardown

Use the same checkout, AWS account, and state used for deployment:

```powershell
aws sts get-caller-identity
terraform -chdir=infra state list
terraform -chdir=infra plan -var="allow_destroy_data=true" -out=teardown-enable.tfplan
# Confirm only the intended bucket force_destroy flags change.
terraform -chdir=infra apply teardown-enable.tfplan
terraform -chdir=infra plan -destroy -var="allow_destroy_data=true"
# Review the exact resources and stored data that will be removed.
terraform -chdir=infra destroy -var="allow_destroy_data=true"
terraform -chdir=infra state list
```

The deletion flag defaults to false and must be applied into state before destroy. It permits
emptying only this stack's buckets, including object versions. Destroy also removes results,
metadata, queue messages, logs, alarms, and IAM resources. Detach any caller-policy attachment made
outside Terraform before destroying the stack. Retain state until deletion succeeds and verify
resource removal in AWS.
