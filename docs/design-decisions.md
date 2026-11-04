# Design decisions

## Why these services

HTTP API + two Python Lambdas keep the system small. Upload bytes go directly to S3 using a signed
POST policy so API Gateway payload limits are irrelevant to the file transfer. DynamoDB is the
source of metadata truth and supports conditional ownership without a separate coordinator.
Private results in a separate bucket cannot trigger ingestion recursively. Terraform describes
the complete stack; it does not provision an AWS account or caller credentials.

IAM is appropriate for a single trusted operator using the AWS credential chain. It avoids running
an identity service. This is not per-user authorization: every permitted caller can access every
dataset ID, and a UUID is not an authorization boundary.

## Event delivery and input identity

[S3 delivers at least once](https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventNotifications.html),
so code must tolerate duplicates. Events can arrive out of order. A presigned POST is reusable;
an attacker holding it can overwrite its key until expiry. Upload bucket versioning lets the
worker read **exactly the version named by its event**, avoiding a HEAD/GET race against newer bytes.
The worker reads at most 1 MiB + 1 byte and verifies server-stored byte length and SHA-256.
Digest metadata in the form is not trusted as proof of content integrity.

A DynamoDB claim atomically pins `input_version`, marks `processing`, sets an attempt token and
60-second lease. The first successfully claimed version wins (not necessarily the earliest upload).
Different-version replays are ignored. Retrying the same version may reclaim only an expired lease.
The lease outlives the 30-second Lambda execution timeout. When an active same-version lease is
observed, the invocation raises `LeaseBusy` so a retry remains available if the owner crashes.

Completion/failure updates require the current attempt token and processing state. Each attempt
writes its own result key before conditionally publishing it. A stale attempt cannot overwrite a
winning result or publish its pointer. Losing/orphan results disappear via lifecycle. This provides
idempotent **visible completion**, not exactly-once function execution or zero duplicate S3 writes.

## Failure handling

Validation failure is deterministic and terminal, stored as a fixed code without file excerpts.
SDK/network/storage failure releases the lease and re-raises, retaining the pinned version.
A hard timeout cannot release ownership; the next retry must wait for lease expiry.
[Lambda async retries](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html)
are configured for two function-error retries and 900-second event age. Exhausted records are
retained in SQS. The failure queue is a diagnostic destination, not an automatic work queue.

No end-to-end exactly-once or guaranteed-completion claim is made. If retries are exhausted, metadata
can remain processing. The API derives `JOB_EXPIRED` after 30 minutes; it does not mutate state on GET.
Late events cannot claim after the deadline. An attempt claimed just before the deadline may finish
after it; until then GET may briefly show expiration, and a successful stored completion takes
precedence over job expiry. Seven-day dataset expiry always takes precedence over stored completion.

Manual recovery before deadline: inspect the failure invocation payload, fix the underlying issue,
then invoke the worker with the original S3 event, preserving the original version ID. Wait for any
lease to expire. Never remove a claim or change a completed record. After deadline, create a new job
and re-upload the original source file; raw S3 versions may already have expired after one day.

Multi-record events process sequentially. If a later record fails, earlier successful records are
safe to see again on retry. Invalid event envelopes/buckets are raised and eventually captured in
the failure destination. S3 notification test events are acknowledged without attempting processing.

## Permissions and sensitive data

All routes require [IAM SigV4](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-access-control-iam.html).
The API role may create/read metadata, sign writes under the upload prefix, and sign reads under
the result prefix. The worker may read upload versions, write results, get/update metadata and
send failed invocation records to its one queue. Neither role lists buckets, deletes objects,
creates tables or reads unrelated resources. Logging permissions name only each function's log group.

S3 blocks public access, disables ACLs, encrypts with SSE-S3 and rejects plaintext transport.
The table and failure queue use service-managed encryption. An optional customer-managed KMS key
would add permissions, cost and operational complexity; it is future work. No secrets are needed in
Terraform variables. Signed URLs/forms, state and plan files stay out of Git. Logs omit raw data.

API POST policies use exact keys, content types, checksum metadata and byte-length conditions;
[Boto3's POST policy interface](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/generate_presigned_post.html)
adds bucket/key constraints. The worker independently verifies actual bytes against the stored
length and digest. All permitted callers share one dataset namespace.

## Limits and cleanup

Limits are named constants in `config.py`; deployment-independent tests exercise their boundaries.
Changing lease/runtime, event age/job deadline, or retention/lifecycle requires coordinated edits
and a review of the failure model. The 10-second validation deadline is cooperative, while the
input cap bounds the initial parse and Lambda imposes a hard 30-second execution limit.
Normalization is intentionally narrow: CSV strings remain strings, JSON scalar types remain typed.
No inferred schema, numeric coercion, query engine or analytics claim.

Metadata TTL and S3 lifecycle provide eventual cleanup. The API checks application expiry because
TTL removal can lag. Abandoned metadata is retained for diagnosis until TTL. Inputs expire sooner
than results to reduce unnecessary raw-data retention. Cleanup is not a data erasure SLA.

## Deployment workflow

CI lints, runs mocked tests, builds the Linux ZIP, validates Terraform and tests its plan without AWS.
Infrastructure deployment uses Terraform and the operator's AWS credential chain. A shared deployment
workflow should use encrypted remote state with locking and a reviewed plan. If GitHub OIDC is added,
scope `aud` to `sts.amazonaws.com` and `sub` to this repository's protected deployment environment.
Do not add stored AWS keys to GitHub. See [deployment](deployment.md) for the operator workflow.
