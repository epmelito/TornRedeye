# Deployment and operation

`template.json` is an AWS SAM / CloudFormation definition for `eu-north-1`.
The `tornredeye-collector` stack is deployed in `eu-north-1`, with its Scheduler
disabled. The region assertion rejects other regions.
The schedule parameter defaults to `DISABLED`; enabling it is a separate
operating action after manual verification.

Use Python 3.11 or newer for the local packager and tests. The template selects
Python 3.13 with its included boto3 SDK. The packager writes
`.aws-sam/collector.zip` with exactly the three application modules; tests,
Git metadata, documentation, and local configuration are excluded. Repackage
before every deployment. No `sam build`, container, or dependency installation
is needed for this standard-library application ZIP.

From the repository root, these PowerShell commands capture offline evidence
using the repository helpers. AWS SAM CLI with cfn-lint must be installed for
the last command; this lint check does not deploy resources.

```powershell
. .\.chatgpt\powershell-helpers.ps1
$env:SAM_CLI_TELEMETRY = '0'
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath python -ArgumentList @('-B', 'tools/package_lambda.py')
    Invoke-NativeCommand -FilePath python -ArgumentList @('-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v')
    Invoke-NativeCommand -FilePath sam -ArgumentList @('validate', '--lint', '--template-file', 'template.json', '--region', 'eu-north-1')
}
```

The tests check packaging and the security/source contracts. They do not replace
SAM/CloudFormation schema validation or a deployed smoke test. Local tests do
not execute the Python 3.13 Lambda runtime or its SDK.

When a stack update is separately authorized, use an existing AWS identity with
stack deployment and IAM role creation/pass permissions. The verified regional
Lambda concurrency quota is 10; the approved configuration does not reserve
concurrency or require a quota increase. This command creates AWS resources and
uploads code to a SAM-managed artifact bucket; it is not an offline check.
It prompts for review of the change set and leaves the schedule disabled.

```powershell
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath sam -ArgumentList @(
        'deploy', '--template-file', 'template.json', '--stack-name', 'tornredeye-collector',
        '--region', 'eu-north-1', '--resolve-s3', '--capabilities', 'CAPABILITY_IAM',
        '--confirm-changeset', '--parameter-overrides', 'ScheduleState=DISABLED'
    )
}
```

Read the stack outputs for the bucket, function, log group, schedule, and group
names. Before enabling, invoke the function once synchronously with an empty
JSON event. Check both the returned payload and Lambda's `FunctionError` field;
an Invoke API success alone does not mean the handler succeeded. Verify the
linked raw bytes and normalized JSON, timestamps, status, and CloudWatch logs.
Verify bucket encryption/public blocking, lifecycle configuration, log retention,
conditional S3 writes, Scheduler trust, and async configuration in AWS. Confirm
YATA availability and source rate limits before starting recurring collection.
Keep scheduling disabled until rate-limit/restriction handling and the recovery
limitations below have been reviewed and addressed as needed for unattended
polling; the local polling test does not close these gaps.

Enable by repeating the SAM deploy command with `ScheduleState=ENABLED` after
review. Disable by restoring `ScheduleState=DISABLED`. Explicitly pass this
parameter on updates so a previously enabled schedule is not left enabled by
parameter reuse. Avoid direct Scheduler edits that create CloudFormation drift.

The intended schedule is `rate(1 minute)`, with flexible windows off, at most one
delivery retry, and a 60-second delivery-age limit. EventBridge Scheduler has
[60-second target invocation precision](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html),
so a one-minute schedule does not guarantee exact or minimum 60-second YATA
request spacing. Delivery jitter, async queueing and duplicate delivery can
shorten gaps or cause overlap. The successful local 12-slot test supports the
interval choice but does not establish AWS timing or ongoing provider permission.
Scheduler invokes Lambda
asynchronously: delivery success is not collection or persistence success.
Lambda has a 120-second timeout, 128 MiB memory, zero function-error retries,
and a 60-second async event-age limit. Concurrency is not explicitly reserved;
the function shares the regional unreserved concurrency pool with other functions.
Overlapping invocations are possible, including manual invocations and duplicate
deliveries. Each retains its own retrieval UUID and evidence; execution is not
serialized. Shared concurrency exhaustion can cause throttling. Throttling/system
errors may still be retried within that age limit. SDK retries and duplicate
service delivery remain possible; every actual handler invocation is a new
retrieval UUID. These settings do not reconstruct an earlier in-memory result.

Inspect the collector logs and Lambda `Errors`, `Throttles`, and
`AsyncEventsDropped` metrics, plus Scheduler delivery failure metrics. A
Scheduler rejection can occur before a handler log exists. This slice adds no
alarms, dead-letter queue, or automatic recovery; expired/failed deliveries can
leave collection gaps. Hard timeouts or S3 failures can leave raw-only or
unpersisted evidence; application exceptions otherwise preserve diagnostics.

The evidence bucket is retained on stack deletion and replacement. Keep its
physical name and preserve its logical ID when updating; replacing it retains
old history but directs new writes to a different bucket. Deleting the stack
stops collection but does not erase or manage retained buckets. Raw evidence
expires after 60 days (S3 lifecycle removal is asynchronous); normalized JSON
has no expiration. Retention does not protect against privileged manual deletion
or lifecycle policy changes. Review data-resource changes in every change set.

At one-minute intervals, the nominal schedule has 1,440 opportunities/day, or
43,200 in 30 days, five times the previous schedule. Usually each actual retrieval
writes two objects; jitter, duplicates, restrictions and delivery failures can
change the request/object counts. Costs depend on Lambda time,
S3 request/data volume, logs, retries, and any retained or deployment artifact
buckets. Normalized history grows without expiry. Disabled scheduling prevents
scheduled invocations, but retained data and deployment artifacts can still cost
money. No account pricing verification has been performed.

## Current recovery limitations

The collector makes one request per invocation and retains HTTP status/body
evidence for failures. Lambda attempts S3 persistence before raising for failed
or malformed collections. It does not retain response headers, honor Retry-After,
or coordinate backoff or pauses across invocations. Later scheduled or duplicate
invocations therefore contact YATA again after HTTP 429 or 401/403. The local
resumable harness's restriction and recovery protections are not implemented in
production. Transient network errors, timeouts and HTTP 5xx remain visible
failures; there is no immediate application retry or catch-up collection.

Each invocation creates a new retrieval UUID. S3 persistence is idempotent only
for the original result and UUID; event redelivery cannot repair an earlier
partial write. Hard interruptions can lose in-memory results or leave raw-only
evidence, with no durable checkpoint or automatic restart recovery. Preserve
existing evidence and unresolved gaps; never claim a later observation recovers
missed stock history. Unchanged valid observations remain successful retrievals.

Possible future improvements include retaining response headers, coordinating
Retry-After backoff and restriction pauses, and recovering interrupted attempts
or partial storage writes. No coordination architecture is approved or implemented.
Any durable state, IAM or data-contract changes require separate review and
authorization. Review HTTP 429 guidance, HTTP 401/403 stopping behavior and
interruption handling before enabling unattended polling. Keep the Scheduler
disabled during that review; manual disabling after a restriction cannot guarantee
that already queued invocations stop.
