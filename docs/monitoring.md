# Production monitoring and resource consumption

Issue #8 adds offline definitions in `template.json`; deployment requires separate
authorization and independent infrastructure/security review. Monitoring uses the
existing collector log group, two dimensionless metrics in
`TornRedeye/${AWS::StackName}`, three standard alarms, and one SNS topic. It adds
no collector AWS API calls, YATA requests, jobs, billing integration, dashboard,
or S3 request monitoring. Collection, evidence retention and polling safeguards
keep their existing behavior.

## Signals and alert behavior

| Signal | Meaning and evaluation |
| --- | --- |
| `PersistedObservations` | Count the existing `persisted collection_id=` log only when it also contains `status=observed`. Emitted after `persist` returns, confirming both raw and normalized writes or matching existing evidence. Observed stock zero and repeated cached observations count. HTTP 200, raw-only writes, skipped runs and persisted missing/malformed/failure records do not count. |
| Missing observations alarm | Sum per minute, `FILL(observations, 0)`, below 1 for 15 of 15 minutes. Silence, missing metrics and explicit zeros breach. One observation in the evaluation window clears the alarm. Detects roughly 15 minutes without persisted observations, including failed schedule delivery or stopped logging. |
| Collection errors alarm | Built-in `AWS/Lambda Errors`, scoped to the collector function, sum >= 1 in at least 3 of 5 one-minute periods. Requires three breaching minutes; three errors confined to one minute do not trigger it. Includes propagated source, configuration, control, persistence and release errors, and runtime timeouts. Missing data is nonbreaching. |
| `PollingHalts` and halt alarm | Count `collection skipped` logs with `reason=halted:`; sum >= 1 in one minute. Covers access/rate halts and abandoned attempts. Ordinary spacing, cooldown, conflict and in-flight skips do not count. Missing data is nonbreaching. A restriction-setting invocation already raises a Lambda error; subsequent halted skips keep this alarm active. |

Metric filters default to zero in minutes with other log events; without events
they publish nothing. The heartbeat fills these gaps explicitly to avoid old
positive samples extending the evaluation window. Minute boundaries, service
evaluation and log delivery latency prevent an exact 900-second guarantee.
Delayed logs can delay recovery or cause a false gap alert. Filters do not
backfill history. They reflect confirmed persistence at log time, not source
freshness, unique exports, or permanent protection against later deletion.
A crash after persistence but before logging can conservatively undercount.
Repeated submission or delivery of matching log events can increment these
metrics more than once for the same collection. Metric filters do not deduplicate
by collection UUID. Their sums are matching-event counts, not exact distinct
collections, distinct halts, new source updates or S3 object counts. For distinct
persisted collections, use existing normalized evidence grouped by collection
UUID with `status=observed`, or deduplicated completion logs with explicit
coverage limits. No new reader or deduplication service is introduced here.
Persistence followed by a control-release failure can produce both an
observation heartbeat and an invocation error; both are valid signals.

All alarms notify on transitions to ALARM and OK; they do not send reminders on
every evaluation. Error OK means fewer than three breaching minutes in the
evaluation window; halt OK means no recent halt signal. Neither is proof
that collection resumed. Confirm recovery through the observation heartbeat,
current logs, original evidence and the existing control inspection procedure.
There is no automatic guard reset or retry. Throttles, dropped async events and
Scheduler delivery failures remain diagnosable through existing AWS metrics;
the heartbeat detects their resulting gaps without extra paid alarms.

## Private notification setup and verification

`OperatorEmail` defaults to empty, which creates no email subscription. Supply
the private operator address through deployment parameters, never repository
files. `NoEcho` masks parameter display but does not hide the SNS subscription
endpoint from authorized AWS operators; do not publish it or parameter files.
The topic output supports separately configured destinations. CloudWatch publish
permission is limited to this account and these three alarm ARNs. The collector
and Scheduler roles gain no permissions.

Alarm actions are enabled only when `ScheduleState=ENABLED`. Disabling the
schedule suppresses ALARM and OK notifications, while alarm evaluation continues.
Manual collection errors during that maintenance state will therefore not email.
Re-enabling actions on an already alarming resource must not be relied on to
send a fresh transition notification: inspect its state explicitly.

For a separately authorized production update, preserve the existing enabled
schedule and bucket identity; use the packaging/validation process in
[deployment and operation](deployment.md). Review the change set for in-place
updates and the SNS policy. Confirm the email subscription and test SNS delivery
before relying on alerts; an empty/unconfirmed destination provides no email
coverage. No deploy command in this document is authorization to deploy.

After authorization, verify both filter patterns against real log records using
CloudWatch's filter test facility. Check an observed persisted line matches,
while HTTP-success, missing, malformed, partial-write and ordinary skip lines
do not. Verify halt lines match the second filter. Review the alarm metric
configuration and ALARM/OK email delivery. Do not create extra YATA requests or
clear restrictions for testing. Use an approved maintenance window to observe
a collection gap, noting notification suppression when the schedule is disabled;
testing alarm actions requires separate approved operator configuration. Confirm
normal scheduled persistence clears the heartbeat afterward. Offline tests and
SAM lint cannot prove AWS ingestion, alarm timing or email delivery.

## Resource indicators for a future dashboard

Reuse the following existing signals. This issue defines their interpretation;
it does not implement a reader, public endpoint or periodic query. Every future
value needs a source, resource scope, UTC window, unit, latest sample time,
coverage and classification (measured, derived, estimated or unavailable).
Missing samples must remain unknown; zero-fill is only an alarm policy.

| Indicator | Source and calculation | Limits |
| --- | --- | --- |
| Lambda invocations, errors, throttles and dropped events | Existing `AWS/Lambda` metrics; sum counts for the collector over the same window. Error rate = Errors / Invocations when Invocations > 0. | Invocations includes skips and failed handler runs; throttled requests do not execute the handler. Invocation counts are not an account billing total. |
| Execution time and compute consumption | `Duration` for operational latency. For billed compute, derive sum(Billed Duration ms / 1000 * Memory Size MiB / 1024) from existing Lambda REPORT records, including skip/failure runs. | Duration excludes initialization and differs from billed duration; Duration * configured memory is only an execution proxy, not billed usage. REPORT coverage must be complete; absent records mean an incomplete subtotal. Do not use Max Memory Used as allocated compute. Historical memory settings come from each report. |
| Archive size, object count and daily growth | Free daily `AWS/S3 BucketSizeBytes` with BucketName and StorageType=StandardStorage; `NumberOfObjects` with StorageType=AllStorageTypes. Latest daily sample; growth = size difference / elapsed days between comparable samples. | Bucket total includes raw, normalized and control objects, not only successful observations. Daily samples can lag or be missing; growth is a net change, not ingestion bytes or a storage bill. Storage classes must be accounted for if they ever change. No paid detailed request metrics. |
| Log ingestion and retained size | `AWS/Logs IncomingBytes` and `IncomingLogEvents`, scoped to CollectorLogGroup; sum over the window. DescribeLogGroups `storedBytes` is an eventual retained-size snapshot if separately read. | Ingestion bytes are uncompressed; retained bytes are compressed and age out under the existing 14-day policy. They are different quantities. IncomingBytes is not a storage total or an exact invoiced quantity. No new scanner or retention policy. |
| Collection efficiency | Derived matching-event ratio `PersistedObservations` / Invocations over the same window. For distinct efficiency or compute per observation, deduplicate existing observed normalized records by UUID and align retrieval/execution windows before dividing. | Log duplicates can inflate the metric ratio, even above 100%; do not clamp it or describe it as distinct success rate. Return unavailable for zero denominator or incomplete coverage. Result logs (`collection_id=... status=... retrieved_at=...`) do not cover requests that fail before logging. Cached observations remain valid but are not new source updates. |

## Cost-consumption reference for Issue B

Public dashboard implementation belongs to Issue B. The reference below reuses
existing metrics, REPORT logs, normalized evidence and the template. It adds no
polling, metrics, services or dependencies. **Measured** means an AWS-reported
sample or stored evidence value; **derived** means arithmetic on those values;
**estimated** means assumptions about execution or billing; **unavailable** means
the required evidence, coverage, eligibility or rate is unknown. Do not render
unavailable values as zero or merge these classifications into a measured bill.

| Cost component | Consumption reference and classification | Cost conversion and missing evidence |
| --- | --- | --- |
| Lambda requests | Measured collector Invocations includes admitted, skipped and failed executions. Derived monthly executed-request subtotal uses Sum over a complete UTC calendar month. A schedule-only scenario estimates 1,440 opportunities/day, or 44,640 in 31 days. | Estimated request cost = request units / 1,000,000 * verified regional request rate. Small scheduled `{}` events use one request unit per execution; manual events, redelivery, async request-size rules and failed delivery mean schedule opportunities are not measured billable requests. Account-wide charged requests remain unavailable. |
| Lambda GB-seconds | Derived from complete REPORT records, deduplicated by Lambda request ID: sum(Billed Duration ms / 1000 * Memory Size MiB / 1024). Includes guard waits, skips, S3 calls, failures and initialization included in billed duration. For current 128 MiB configuration, this is 0.125 * total billed seconds. | Estimated compute cost = derived GB-seconds * verified regional x86 on-demand rate. No fixed average duration is assumed. `Duration` is an operational measurement, not billed duration; incomplete REPORT coverage gives an incomplete subtotal, not a full month. |
| S3 storage | Measured daily bucket bytes; derived time-weighted monthly storage = sum(sample GB * covered days) / days in month, with explicit gaps and price-list GB units. Stored raw byte lengths can describe raw payload growth only. | Estimated storage cost = derived GB-month * verified regional Standard storage rate. A latest-size * rate snapshot is a run-rate estimate, not month's consumption. Raw payload bytes omit normalized objects, control data and applicable storage overhead. Artifact buckets are outside the evidence-bucket scope. |
| S3 GET/PUT operations | Estimated application operations from the execution paths below, optionally informed by deduplicated existing logs and receipts. Exact SDK wire-request and billed-operation totals are unavailable from current evidence; daily object counts do not measure requests. | Estimated request cost = GET attempts / 1,000 * verified GET rate + PUT attempts / 1,000 * verified PUT rate, under stated billing assumptions. Include guard operations, partial writes and comparisons. Retries and error charging prevent equating application calls with charged requests. Do not enable paid S3 request metrics for this estimate. |
| CloudWatch log ingestion/storage | Measured IncomingBytes over covered windows; derived ingestion GB using price-list units. Retained compressed bytes and time-weighted storage are separate from uncompressed ingestion. | Estimated ingestion cost = GB * verified ingestion rate; estimated storage cost = GB-month * storage rate. Fourteen-day retention limits reconstruction from log records. Query scanning and metric-read costs are separate and unavailable unless the corresponding usage is known; this issue adds no queries. |
| CloudWatch monitoring resources | Defined resource inventory: two custom metrics and three standard alarm metrics. Derived chargeable resource-month estimates depend on metric emission hours and alarm existence hours. | Estimated full-month baseline is $0.60 metrics + $0.30 alarms at the rates below. This inventory is not measured account usage. Basic AWS metrics are reused; neither the 3-of-5 change nor Issue B's reference adds a metric or alarm. |
| SNS publishes and deliveries | Measured existing `AWS/SNS NumberOfMessagesPublished`, `NumberOfNotificationsDelivered` and `NumberOfNotificationsFailed`, Sum with TopicName for this monitoring topic. Derived covered-window totals; inactive/missing samples remain unknown unless coverage establishes zero activity. | Publish count is not total billed API request units. A scenario estimate uses alarm transitions * confirmed subscribers for deliveries, with publish-size units, failures, retries, tests and subscription changes stated separately. Topic delivery metrics mix protocols if other destinations are added; email-only totals then become unavailable without protocol-specific evidence. Delivery success does not prove an operator read the email. No fixed notification volume is assumed. |

Use the existing [SNS metric definitions](https://docs.aws.amazon.com/sns/latest/dg/sns-monitoring-using-cloudwatch.html)
for notification measurements. Resolve rates from the applicable region,
architecture, storage class, operation and effective date using the linked AWS
pricing references. A subtotal obtained by multiplying measured or derived
consumption by public list prices is still an **estimated gross cost**. Do not
call it the billed amount or subtract account allowances from a project subtotal
as guaranteed savings. Exact invoice cost and remaining credits are unavailable
without account billing evidence, which is outside Issues #8 and B's reference.

### S3 request estimation from the current execution paths

Count application-level calls before SDK retries. These scenarios are estimates,
not an upper bound or an exact bill:

| Invocation path | GET attempts | PUT attempts | Provider retrievals |
| --- | --- | --- | --- |
| Admitted result with response bytes and completed release | 1 control read, or 2 when spacing waits and rereads | 3 control writes (acquire, outcome, release) + 2 evidence writes = 5 | 1 |
| Admitted result without response bytes and completed release | 1 control read, or 2 after waiting | 3 control writes + 1 normalized evidence write = 4 | 1 |
| Ordinary halt, cooldown, active attempt or insufficient-budget skip | 1 control read; 2 if an eligible spacing wait rereads before skipping | 0 | 0 |
| Acquisition conflict | 1 control read, or 2 after waiting | 1 attempted acquisition write | 0 |
| First abandoned-attempt halt | 1 control read | 1 attempted halt write; subsequent halted invocations only read | 0 |
| Failure before control read | 0 | 0 | 0 |

For `R` invocations reaching the initial control read, `W` spacing rereads and
`C` evidence comparison reads, estimate GET attempts as `R + W + C`. For a
mixed window, estimate PUT attempts as acquisition attempts + outcome-write
attempts + release attempts + raw-write attempts + normalized-write attempts +
abandoned-attempt halt-write attempts. This expanded expression handles conflict
and partial-failure paths; do not blindly multiply observed collections by five.
A raw-write failure prevents the normalized write and release. An outcome-policy
failure still attempts evidence preservation but prevents release. A source
failure can still write evidence and release. A 412 on an evidence PUT performs
an additional GET comparison; matching existing receipts therefore do not mean
no PUT was attempted. SDK retries can issue further GETs/PUTs, and lost
acknowledgements obscure how many reached AWS. Current evidence does not expose
all such attempts or their billing treatment.

As an explicitly hypothetical 31-day scenario, 44,640 invocations all reaching
the guard produce 44,640 initial control GET attempts even if every run skips.
If `A` are admitted, all have response bytes and finish normally, and there are
no conflicts, stale attempts, comparisons or retries, PUT attempts are `5 * A`
and GET attempts are `44,640 + W`. Setting `A=44,640`, `W=0` gives 223,200 PUT
and 44,640 GET attempts as a comparison scenario, **not** a cadence forecast:
the 60-second post-completion spacing and bounded wait can reduce admissions.
Derive `A` from deduplicated evidence/covered logs where possible; do not assume
one collection per schedule minute or use duplicate-prone metric sums as exact
request counts. Additional invocations, interrupted paths, manual operations and
SDK retries invalidate that simplified scenario. This reference adds no S3 calls.

See AWS's [Lambda metric definitions](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html),
[S3 metric dimensions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html),
and [log metric definitions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CloudWatch-Logs-Monitoring-CloudWatch-Metrics.html).
Month-to-date totals require complete history, not a current rate extrapolated
as measured usage. Existing logs retain only 14 days, so a full month cannot be
reconstructed from late REPORT/log queries without separately approved exports.
Longer-range metric queries must respect CloudWatch aggregation/retention.

## Allowance and cost indicators

Keep service limits, project budgets and account free allowances separate.
The one-minute schedule gives nominal opportunities, not an observation quota.
The regional concurrency quota of 10 recorded in deployment guidance is a shared
operational quota; concurrency / 10 is not a free-tier consumption percentage.
Use a current verified quota and the matching concurrency scope for that ratio.

AWS currently advertises monthly allowances of 1 million Lambda requests and
400,000 GB-seconds; CloudWatch advertises 10 custom/detailed metrics, 10 eligible
standard alarm metrics, and 5 GB each for log ingestion, archive storage and
Insights scanning. SNS advertises 1 million requests and 1,000 email deliveries.
These are account-wide reference allowances, not a reserved TornRedeye budget.
Account eligibility, other workloads, cross-region usage and credits are
unverified. S3 allowance/credit eligibility depends on the account's plan and
creation date. Where verified legacy S3 eligibility applies, its reference
allowances are 5 GB Standard storage, 20,000 GET and 2,000 PUT/COPY/POST/LIST
requests per month for the eligible introductory period. Newer plans use
time-limited credits; do not assume those legacy allowances apply, or treat
credits as recurring capacity. Sources: [Lambda pricing](https://aws.amazon.com/lambda/pricing/),
[CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/),
[SNS FAQ](https://aws.amazon.com/sns/faqs/), and
[S3 pricing](https://aws.amazon.com/s3/pricing/). The legacy quantities are
documented in AWS's [S3 allowance reference](https://aws.amazon.com/blogs/storage/s3-storage-class-price-reductions/);
that historical offer is not evidence of this account's current eligibility.
Verify S3 eligibility and the legacy limits against the account's actual offer
before displaying them. No eligibility, remaining allowance or savings has been
measured here. Monitoring resources, log ingestion and SNS traffic share their
respective allowances with other account workloads. Different units (GB,
GB-month, GB-seconds, requests, deliveries and resource-months) require separate
indicators; never combine them into one free-tier percentage.

For each future allowance indicator, store its verified amount, unit, account
scope, validity dates and source. Only complete usage over the matching scope
and window supports `usage / allowance` or `max(allowance - usage, 0)`.
Project usage divided by an advertised allowance may be shown as a **project
contribution reference**, never account allowance remaining. If eligibility or
account usage is unknown, remaining allowance is unknown. Proposed advisory
thresholds are 80% warning and 100% reached for verified budgets/allowances;
they are visualization conventions, not deployed alarms, spending caps or
permission to stop polling or delete evidence. Estimates must label assumptions
and exclude unsupported savings or remaining credit balances.

## Expected incremental monthly cost before deployment

Public rates checked on 2026-10-10 from the
[AWS Stockholm CloudWatch price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/eu-north-1/index.json):
first-tier custom metrics $0.30/metric-month, standard alarms
$0.10/alarm-metric-month, Standard log ingestion $0.54/GB and retained log
storage $0.028/GB-month. No account billing API was used.

| Increment | Full-month assumption before allowances | Monthly USD |
| --- | --- | --- |
| Two custom log-filter metrics | Both emit throughout the month; hourly prorating can reduce this | 0.60 |
| Three standard alarm metrics | Heartbeat math references one metric; the other alarms each reference one | 0.30 |
| Existing persistence log gains a status token | Up to 25 additional ASCII bytes per completed persistence. At 44,640 nominal schedule opportunities in a 31-day month, about 1.12 MB extra ingestion, excluding duplicates/manual invocations | Less than 0.001 ingestion plus storage in this stated scenario |
| SNS | State-transition notifications, one confirmed email subscriber; actual volume unknown. Advertised paid rates: $0.50/million requests and $2/100,000 email deliveries | Variable; do not invent notification volume |

The error alarm now requires three breaching minutes out of five; it can reduce
transient-error notifications but does not change resource charges. No numeric
SNS saving is projected. Duplicate log increments do not create additional
metric dimensions, but can distort metric counts and include additional ingested
log bytes. The log-growth scenario above assumes no duplicate submissions.

Expected baseline is **about $0.90/month plus variable notifications and tiny
log growth**, before taxes, free allowances or credits. It can be approximately
$0 incremental if eligible unused account allowances cover these resources and
traffic; this is conditional, not verified. Alarm charges continue when actions
are disabled. No additional scheduled Lambda executions or S3 requests are
introduced; the tiny formatting overhead has not been measured as billed compute.
Existing collection, archive growth and packaging costs are outside this
increment. Future metric reads and Logs Insights scans can incur charges,
especially GetMetricData, which is excluded from the general API free allowance.
Recheck regional rates and account eligibility before deployment; this monitoring
does not cap spend or measure an exact AWS bill.
