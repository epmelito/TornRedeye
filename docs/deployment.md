# Deployment and operation

`template.json` is an AWS SAM / CloudFormation definition for `eu-north-1`.
It has not been deployed. The region assertion rejects other regions.
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

When deployment is separately authorized, use an existing AWS identity with
stack deployment and IAM role creation/pass permissions. Check Lambda reserved
concurrency quota availability first. This command creates AWS resources and
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

Enable by repeating the SAM deploy command with `ScheduleState=ENABLED` after
review. Disable by restoring `ScheduleState=DISABLED`. Explicitly pass this
parameter on updates so a previously enabled schedule is not left enabled by
parameter reuse. Avoid direct Scheduler edits that create CloudFormation drift.

When enabled, Scheduler delivers every five minutes with flexible windows off, at most one
delivery retry, and a 60-second delivery-age limit. It invokes Lambda
asynchronously: delivery success is not collection or persistence success.
Lambda has a 120-second timeout, 128 MiB memory, reserved concurrency 1, zero
function-error retries, and a 60-second async event-age limit. Throttling/system
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

At five-minute intervals, normal operation makes 288 retrievals/day, or 8,640
in 30 days, usually writing two objects each time. Costs depend on Lambda time,
S3 request/data volume, logs, retries, and any retained or deployment artifact
buckets. Normalized history grows without expiry. Disabled scheduling prevents
scheduled invocations, but retained data and deployment artifacts can still cost
money. No account pricing or live quota verification has been performed.
