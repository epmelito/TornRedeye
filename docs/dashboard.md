# Public operations dashboard

Issue [#11](https://github.com/epmelito/TornRedeye/issues/11) and
[approved decisions](https://github.com/epmelito/TornRedeye/issues/11#issuecomment-6102066164)
define this offline MVP. `dashboard-template.json` is a separate SAM/CloudFormation
stack; `template.json`, collector code, schedule, safeguards and evidence remain
unchanged. Deployment and independent infrastructure/security review are still
required. This document is the dashboard operating contract; see
[monitoring.md](monitoring.md) for collector signal and consumption semantics.

## Architecture and publication

An hourly Lambda (Python 3.13, runtime boto3, 128 MiB x86, 90-second timeout)
publishes only `public/data.json`. The unversioned private encrypted S3 bucket
also holds three static assets. No hourly archive, database, frontend framework,
new custom metrics, alarms, provider requests, billing API, or visitor AWS calls
are introduced. An EventBridge rule starts disabled; automatic delivery and
asynchronous execution retries are disabled. No reserved concurrency consumes
the existing low account quota.

CloudFront uses its default domain and pay-as-you-go pricing, PriceClass_100,
HTTPS-only viewer access, a regional S3 REST origin, and OAC with `always` SigV4
signing. The bucket blocks public access and allows CloudFront read access only
to `public/*` for this exact distribution ARN. TLS is required for all S3 access.
The origin has no website endpoint or public ACL. No access logs are enabled
(no additional log storage or visitor information). Security response headers
restrict scripts, styles and requests to the same origin, forbid frames and
disable MIME sniffing. Only GET/HEAD are served. Cache TTL is normally five minutes,
with no cookies or query parameters forwarded. No invalidation on hourly updates.

Every public field is constructed from a fixed allowlist. Only validated numeric
telemetry and UTC timestamps from normalized evidence are exposed; AWS responses,
log messages, identities, UUIDs, ARNs, bucket names, keys, provider records,
credentials, operator destinations and exception details are never copied.
Frontend text uses `textContent`, not HTML interpolation. Missing values remain
null/unavailable and measured zero remains zero.

The page presents recent gap candidates and observation/provider freshness before
seven-day history. Hourly trends and values remain available in an expandable
section. Consumption values retain units and availability-aware classifications;
coverage, provenance and UTC windows are accessible through each row's details.
On narrow screens, consumption rows become labelled entries rather than requiring
horizontal scanning of the primary values. A successful query with no numeric
samples still displays unavailable, while reported zero remains measured data
(or a derived zero subtotal). These presentation rules do not establish complete
coverage or alter the published data contract.

Retrieval errors are isolated by source. A snapshot can be valid and partial;
`available` means the queries/selected sources were available, **not** complete
historical coverage. Even an exhausted log query or successful metric query can
omit delayed, missing, duplicated, expired or never-emitted records. Published
coverage labels preserve that uncertainty. UTC publication older than two hours
is stale, independently of observation retrieval and provider source/export ages.
`as_of_utc` records the retrieval cutoff; `published_at_utc` is set immediately
before the successful atomic publication attempt, independently of that cutoff.
The browser checks publication age every minute without additional network calls.
It fetches the JSON once per page load, with caching revalidation, and never
queries AWS. Reload obtains a newer publication.

S3 publication is one atomic conditional PUT: initial `IfNoneMatch=*`, later
`IfMatch` on the previous ETag. An older overlapping invocation cannot replace
a snapshot with a newer retrieval cutoff. A failed read, conflicting write,
serialization or size failure leaves the previous snapshot intact. A lost PUT acknowledgement has an
uncertain outcome: S3 contains either the previous object or the new complete
valid snapshot, never a partially written JSON object. No delete or rollback of
canonical evidence is attempted.

## Retrieval bounds and provenance

| Source | Maximum work per invocation | Meaning and limits |
| --- | --- | --- |
| CloudWatch `GetMetricStatistics` | 16 metrics over seven days, plus one three-hour minute heartbeat query: 17 calls total, no pagination | Completed UTC hours, 168 samples per hourly series; S3 uses seven completed daily slots. Missing samples are null. Sum of returned samples is a derived subtotal, never an exact period total. |
| Collector logs `FilterLogEvents` | Two queries (`persisted status=observed`, `REPORT`), each two-hour window, at most two pages of 100 events | At most four calls / 400 returned events; ascending service order, so a capped query may miss the newest events. Pagination truncation is explicit. No Insights scans or older log scans. |
| Normalized evidence | At most three exact keys obtained from validated persistence UUIDs in those logs; 64 KiB maximum each | Read only `normalized/yata/jap/206/`. Validate schema, observed status, matching identity and timestamps. First valid newest-log candidate supplies freshness, not a guarantee of latest evidence. No bucket listing, raw reads, date/archive scanning or polling-control access. |
| Previous public JSON | One GET, at most 192 KiB + overflow byte | Used only for ordering and conditional publication; invalid/oversized previous state fails closed. |
| Publication | One PUT; JSON at most 192 KiB | Fixed key, no archive; three separately uploaded static assets. |

At most 26 logical API calls (17 metrics + 4 logs + 3 evidence + 2 publication).
SDK attempts are capped at two per operation, with 2-second connection and
3-second read timeouts. Retrieval starts no new operation once fewer than
40 seconds remain, reserving time for publication. Lambda timeout is the ultimate
90-second bound. Repeated API failures may prevent later retrieval sources;
those sources are explicitly unavailable. Metrics payload is bounded by fixed
query windows/periods (maximum 180 points per request), logs by fixed pages/events,
evidence and publication by byte limits. No query work increases with archive size.

The publisher role has no evidence writes, S3 listing, control access, SNS calls,
Lambda management or collector invocation permissions. It can read selected
normalized evidence, read/write only its exact JSON key, query the exact collector
log group, and write its own seven-day log group. Both PUT permission statements
require a conditional request. `GetMetricStatistics` requires IAM Resource `*`
(CloudWatch does not support resource-level scoping for this action); the code
fixes metric names, dimensions and windows to existing configured telemetry.
Configuration is private and must point to the approved collector resources.

Hourly reliability comprises persistence and halt **matching-event counts**, plus
Lambda invocations, errors and throttles. Duplicated matching logs can inflate
counts; crashes before persistence logging can undercount. Recent candidates
are consecutive runs of at least 15 minutes without a returned positive heartbeat.
Their unknown-minute count separates explicit zero samples from missing telemetry;
neither proves a true persisted-observation absence. No coverage percentage or
exact distinct-collection count is inferred.

Consumption uses Lambda Invocations and execution Duration, daily S3 standard
storage/object samples, collector uncompressed log ingestion, and SNS published,
delivered and failed notification samples. REPORT billed duration × memory / 1024
/ 1000 gives a deduplicated recent **GB-second subtotal**; request IDs stay private.
No REPORT matches means unavailable, not zero. Execution Duration is not billed
duration. SNS metrics are not billable request counts or email-only delivery.
Stored-log size, S3 actual request operations, CloudFront traffic, whole-account
allowance consumption and actual bills remain unavailable. The UI gives conditional
allowance references and the collector's operation model, not fabricated balances.

## Incremental costs and assumptions

The dashboard creates no custom metric or alarm charges. Existing Issue #8
monitoring's two custom metrics and three alarms remain an existing baseline,
not an increment attributable to this stack. Metric reads use GetMetricStatistics
instead of GetMetricData (the latter is excluded from the general API allowance).
17 × 744 = 12,648 metric requests in a 31-day month, plus at most 2,976 log read
requests; SDK retries can double attempts. No paid Logs Insights scan is used.

At 744 hourly invocations, 128 MiB and **assumed** billed durations of 5–90 seconds,
the publisher uses 465–8,370 GB-seconds. At reference x86 first-tier rates
$0.20/million requests and $0.0000166667/GB-second, the Lambda-only scenario is
approximately **$0.00790–$0.13965/month before allowances**. Five seconds is an
illustrative scenario, not measured runtime; 90 seconds is the configured execution
timeout, not a strict billing ceiling (initialization/overhead, manual invocations
and delivery duplicates can add usage). No total monthly bill is claimed.

Successful hourly publication attempts one S3 PUT and one prior-snapshot GET,
plus 0–3 normalized GETs: about 744 PUT and 744–2,976 GET operations/month before
retries/manual invocations, plus static upload PUTs and CloudFront origin misses.
At Stockholm Standard request rates $0.005/1,000 PUT and $0.0004/1,000 GET,
publisher requests alone are about $0.0040–$0.0049/month. Recheck Stockholm rates
and storage class before deployment. Rates were verified in the public regional
[S3 offer](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-north-1/index.json)
and [Lambda offer](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/eu-north-1/index.json)
on 2026-10-10. Only one ≤192 KiB JSON and three small
assets are stored; origin reads depend on public traffic and cache misses.
Collector operation estimates and their skip/retry uncertainty are unchanged;
see monitoring.md. Log storage/ingestion, S3 storage, API attempts outside remaining
allowances, and CloudFront traffic add variable costs. Under low traffic and
available account allowances, the incremental recurring cost should be pennies;
it cannot be guaranteed or capped by this MVP. No unsupported visitor-volume
forecast or archive-storage estimate is used.

Conditional account-wide references: Lambda 1M requests / 400,000 GB-seconds;
CloudWatch 10 custom metrics / 10 standard alarm metrics / eligible API requests
and log allowances; SNS 1M API requests / 1,000 email notifications; CloudFront
pay as you go 1 TB transfer / 10M HTTP/HTTPS requests. S3's legacy introductory
allowances are not universal; newer accounts may use credits instead. Existing
workloads compete for allowances. The publisher does not query account eligibility,
remaining allowances or billing. Rates/terms checked 2026-10-10 against
[Lambda pricing](https://aws.amazon.com/lambda/pricing/),
[CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/),
[S3 pricing](https://aws.amazon.com/s3/pricing/),
[SNS pricing](https://aws.amazon.com/sns/pricing/) and
[CloudFront FAQ](https://aws.amazon.com/cloudfront/faqs/).
Recheck before deployment; estimates are not actual billed amounts.

## Offline validation and future deployment

Run from the repository root:

```text
python -m tools.package_dashboard
python -m unittest discover -s tests
node tests/test_dashboard_frontend.cjs
sam validate --lint --template-file dashboard-template.json --region eu-north-1
git diff --check
```

The generated `.aws-sam/dashboard-publisher.zip` contains only
`dashboard_publisher.py` and uses deterministic atomic packaging. Boto3/botocore
come from the Lambda runtime; no vendored dependency is needed. Production
packaging should use committed LF source, following the existing collector release
practice, after review and commit. Do not deploy an unreviewed working tree.

After separate deployment authorization: package this template independently,
deploy a new stack in eu-north-1 with ScheduleState DISABLED, and pass the existing
collector stack's evidence bucket, function, log group, metric namespace and SNS
**topic name** (not its ARN) as parameters. Do not update the collector stack.
Upload `dashboard/index.html`, `styles.css`, `app.js` only to corresponding
`public/` keys with correct MIME types and five-minute cache control. Do not upload
repository source or evidence. Invoke publisher once, inspect the sanitized JSON
and browser, verify direct S3 access denied / HTTPS CloudFront read works, then
enable this stack's hourly rule. No AWS resources have been deployed or account
telemetry queried during offline implementation. Deployment, live permissions, telemetry availability, caching and
provider failures need separately authorized verification.

If publication stops, retain the previous JSON and show stale publication after
two hours. Operators use private publisher logs and existing Lambda metrics;
there are no additional alarms. Disable only the dashboard hourly rule to pause
publication. The retained hosting bucket needs separate intentional cleanup if
the stack is removed; never clean up the evidence bucket. Traffic can incur costs
even when publication is paused. Independent security/infrastructure review is
required before production deployment.
