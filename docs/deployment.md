# Deployment and operation

`template.json` is an AWS SAM / CloudFormation definition for `eu-north-1`.
The `tornredeye-collector` stack is deployed in `eu-north-1`, with its Scheduler
disabled. The region assertion rejects other regions.
The schedule parameter defaults to `DISABLED`; enabling it is a separate
operating action after manual verification.

Use Python 3.11 or newer for the local packager and tests. The template selects
Python 3.13 with its included boto3 SDK. The packager writes
`.aws-sam/collector.zip` with only the required application modules; tests,
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
names. Initialize the control object as described below before the first invocation
of the updated handler. Before enabling, invoke the function once synchronously
with an empty JSON event. Check both the returned payload and Lambda's `FunctionError` field;
an Invoke API success alone does not mean the handler succeeded. Verify the
linked raw bytes and normalized JSON, timestamps, status, and CloudWatch logs.
Verify bucket encryption/public blocking, lifecycle configuration, log retention,
conditional S3 writes, Scheduler trust, and async configuration in AWS. Confirm
YATA availability and source rate limits before starting recurring collection.
Keep scheduling disabled until the safeguards, operator recovery procedure and
crash limitations below have been independently reviewed and verified in AWS.
The previous production smoke test verified evidence storage, not these new
control-state permissions or runtime SDK conditional-write support.

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
deliveries. The S3 guard admits only one YATA attempt at a time; skipped invocations
log their reason and return a successful `skipped` status without contacting YATA
or creating collection evidence. Shared concurrency exhaustion can cause
throttling. Throttling/system errors may still be retried within that age limit.
SDK storage retries and duplicate service delivery remain possible. Each admitted
retrieval has its own UUID; these settings do not reconstruct an earlier result.

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
change the request/object counts. Each admitted attempt normally adds one
control GetObject and three conditional control PutObject operations; ordinary
skips still perform one control read and emit a log. A spacing wait adds one
control re-read and up to 15 seconds of billed Lambda time. Costs depend on Lambda time,
S3 request/data volume, logs, retries, and any retained or deployment artifact
buckets. Normalized history grows without expiry. Disabled scheduling prevents
scheduled invocations, but retained data and deployment artifacts can still cost
money. No account pricing verification has been performed.

## Polling safeguards and recovery

The handler conditionally updates one control object:
`control/yata/jap/206/polling.json`. It requires an SDK supporting
`put_object(IfMatch=...)`. The Lambda role can read that exact key and update it
only with an [If-Match condition](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html);
it cannot initialize or delete it. Raw and
normalized evidence permissions and retention remain separate and unchanged.
The control object has no lifecycle expiration. Every update changes a revision
UUID as well as the content, preventing reuse of an old ETag after a reset.

An invocation must acquire an eligible control state before making one YATA
request. Concurrent conditional-write conflicts, active attempts, provider
cooldowns and halts skip without contacting the provider. Missing, unreadable,
malformed or future-dated state fails the invocation before collection. There is no fallback
that ignores coordination.

The next eligible time remains at least 60 seconds after completion and release,
rather than 60 seconds after the previous request started. An invocation arriving
up to 15 seconds early may wait **once**, only when `spacing_only` explicitly
identifies ordinary request spacing. Waiting requires enough remaining Lambda
time for the delay plus a 60-second execution reserve. A missing runtime budget
disables waiting; insufficient remaining time skips collection. The wait is
logged. After waking, the handler re-reads control state and acquires it
conditionally; it never waits again or bypasses a changed deadline/restriction.
Halts, active/stale attempts, unknown state and provider cooldowns never wait,
even shortly before cooldown expiry. Older control objects without the optional
boolean `spacing_only` remain valid and default to false, disabling waiting.
Normal completed outcomes set it true; restrictions set it false.

This improves practical frequency without guaranteeing one observation per
scheduled minute. With four seconds of work per retrieval, a deterministic
zero-jitter simulation collects 10 observations across 12 opportunities, with
request starts at 0, 64, 128, 192, 300, 364, 428, 492, 600 and 664 seconds.
Continued work accumulates delay until the 15-second wait cap forces a skip;
in that idealized repeating case the long-run average is approximately 75
seconds per observation. Shorter work can approach one minute; longer work,
jitter, competing invocations, restrictions or failures reduce frequency.
EventBridge timing and provider response latency make the deployed cadence
variable. There is no exact 60-second cadence or replay of skipped opportunities,
and 1,440/day remains a nominal schedule-opportunity estimate.

Spacing relies on AWS runtime UTC clocks and the deployed 120-second Lambda
timeout. The 60-second execution reserve is not a guarantee that S3 finishes;
hard timeout and crash protections still apply. An attempt expires after 180
seconds, but expiry does **not** authorize takeover: an abandoned attempt is
halted for operator review because its response and restriction may be unknown.

The collector retains Date and Retry-After headers in normalized evidence,
including HTTP failures and partial error bodies. Valid Retry-After accepts
nonnegative delay-seconds or HTTP-date (including obsolete HTTP-date formats).
Dates use the later of the absolute deadline and the server Date-relative delay
measured from retrieval, avoiding an early retry when the server clock trails
the runtime. Duplicate, invalid or overflowing guidance is recorded as invalid.
HTTP 429 persists a cooldown to at least that deadline; absent or invalid
guidance persists a halt. HTTP 401/403 persists a halt regardless of guidance.
HTTP 5xx with valid guidance also respects its deadline. Other transient network
errors, timeouts and HTTP 5xx can try at the next eligible opportunity. There
are no immediate application retries or catch-up requests.

Restriction policy is saved while the attempt remains held, before the raw and
normalized evidence writes. Policy-update failure still attempts to preserve
the original evidence, then fails without releasing the attempt. Evidence or
release failures are visible invocation errors. A restriction saved before an
evidence failure remains effective. An uncertain S3 acknowledgement is treated
conservatively; automatic retries never clear a halt or replace a stale attempt.

## Operator inspection and resumption

These are operating procedures for a separately authorized deployment, not
actions performed by the offline implementation. Keep the Scheduler disabled
while initializing or recovering state, stop manual invocations, and allow at
least five minutes for already queued/running invocations to drain. Confirm
recent Lambda logs before editing state; disabling the Scheduler alone does
not stop queued invocations. Review provider restrictions, the attempt UUID,
timestamps and all available raw/normalized evidence. For HTTP 401/403 or
unguided 429, resolve the access/rate restriction before explicitly resuming.
For stale attempts, establish that no invocation can still make a request;
expiry alone is insufficient. Do not resume if that cannot be established.

Use a current AWS CLI supporting S3 If-Match and If-None-Match. The operator
identity needs GetObject/PutObject on the control key; do not expand the Lambda
role. Set `$bucket` from the existing stack's evidence-bucket output. Use the
repository PowerShell helpers for captured evidence.

For **first initialization only**, prepare a ready state and a conditional
creation request in a temporary directory:

```powershell
. .\.chatgpt\powershell-helpers.ps1
$controlKey = 'control/yata/jap/206/polling.json'
$work = Join-Path $env:TEMP ('tornredeye-control-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
$stateFile = Join-Path $work 'state.json'
$requestFile = Join-Path $work 'request.json'
$now = [DateTimeOffset]::UtcNow
$state = [ordered]@{
    schema_version = 1
    spacing_only = $false
    revision = [guid]::NewGuid().ToString()
    updated_at = $now.ToString('o')
    not_before = $now.AddSeconds(60).ToString('o')
    halt_reason = $null
    attempt = $null
}
$utf8 = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($stateFile, ($state | ConvertTo-Json -Depth 5), $utf8)
$request = @{ Bucket = $bucket; Key = $controlKey; ContentType = 'application/json'; IfNoneMatch = '*' }
[System.IO.File]::WriteAllText($requestFile, ($request | ConvertTo-Json), $utf8)
Invoke-CopyOutput {
    Invoke-NativeCommand aws @('s3api', 'put-object', '--region', 'eu-north-1',
        '--cli-input-json', ('file://' + $requestFile), '--body', $stateFile)
}
```

Creation must fail if the key exists; inspect it rather than replacing it.
A missing/unreadable object after the system has operated is an incident, not
permission to automatically reinitialize.

For **inspection and recovery**, use the variable/path setup above with a fresh
temporary directory, omitting the creation request. Retrieve the object and its
current ETag and preserve this snapshot before editing:

```powershell
Invoke-CopyOutput {
    $metadataText = @(Invoke-NativeCommand aws @('s3api', 'get-object',
        '--region', 'eu-north-1', '--output', 'json',
        '--bucket', $bucket, '--key', $controlKey, $stateFile))
    $metadataText
    $script:controlMetadata = ($metadataText -join [Environment]::NewLine) | ConvertFrom-Json
    Get-Content -LiteralPath $stateFile
}
```

After explicit operator review, prepare a new state with the original six fields
and spacing_only set to false,
schema_version 1, a new revision UUID, updated_at set to current UTC,
halt_reason and attempt set to null, and not_before set to the later of the
existing cooldown and current UTC plus 60 seconds. Archive the original state
locally first. Do not clear an outstanding cooldown or edit evidence objects.
Use the inspected ETag in a conditional update request (JSON preserves its quotes):

```powershell
$request = @{ Bucket = $bucket; Key = $controlKey; ContentType = 'application/json'; IfMatch = $controlMetadata.ETag }
[System.IO.File]::WriteAllText($requestFile, ($request | ConvertTo-Json), $utf8)
Invoke-CopyOutput {
    Invoke-NativeCommand aws @('s3api', 'put-object', '--region', 'eu-north-1',
        '--cli-input-json', ('file://' + $requestFile), '--body', $stateFile)
}
```

On a conflict or uncertain acknowledgement, re-read and review; never fall back
to an unconditional write. Read back the accepted state and inspect logs before
a separately approved smoke test or schedule enablement. A malformed state
requires deliberate reconstruction under the same ETag condition; an unreadable
state requires fixing access before proceeding.

## Crash recovery limits

Each admitted attempt has a new retrieval UUID. Evidence persistence remains
idempotent only for the original result and UUID. Redelivery cannot reconstruct
an earlier response or repair its partial write. A hard timeout can lose
in-memory bytes; interruption or S3 failure can leave raw-only or unpersisted
evidence. The control object records coordination and restrictions, not a
response checkpoint. Stale attempts pause rather than pretending to recover
lost observations. Preserve valid history and unresolved gaps; never claim a
later retrieval recovers missed stock history. Unchanged valid observations
remain successful retrievals.

Automatic partial-write reconstruction, alarms and operator tooling are possible
future improvements and are not implemented. Review restriction handling,
conditional-write support, permissions and interruption behavior in AWS before
enabling unattended polling. Keep the Scheduler disabled during that review.
