# Public operations dashboard

Issue [#11](https://github.com/epmelito/TornRedeye/issues/11) and
[approved decisions](https://github.com/epmelito/TornRedeye/issues/11#issuecomment-6102066164)
define the bounded publisher contract. `dashboard-template.json` describes the
separate dashboard stack; frontend changes do not require a collector stack update.
This document describes behavior and operating procedures, not the current deployed
revision or live scheduler state. Production changes require separate authorization.
See
[monitoring.md](monitoring.md) for collector signal and consumption semantics.

## Architecture and publication

A Lambda configured for hourly publication (Python 3.13, runtime boto3, 128 MiB
x86, 90-second timeout) publishes only `public/data.json`. The EventBridge schedule
defaults to disabled; activation requires separate authorization. The public
snapshot contains no scheduler configuration or live schedule status.
The unversioned private encrypted S3 bucket also holds five static assets.
No hourly archive, database, frontend framework,
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

Issue [#30](https://github.com/epmelito/TornRedeye/issues/30), its
[approved presentation specification](https://github.com/epmelito/TornRedeye/issues/30#issuecomment-6179329018)
and [interactive refinement](https://github.com/epmelito/TornRedeye/issues/30#issuecomment-6179427837)
define three static pages sharing `app.js`, `styles.css` and the existing snapshot:

- `index.html` (Operations) explains the project, then shows activity and errors.
  Shared controls default to 24 completed hours, hourly. Visitors can choose
  24 hours or seven days at hourly, three-hour, six-hour or daily intervals.
  Stop notices and capacity blocks summarize the latest 24 returned hours, not
  current restriction status. The three-hour minute timeline distinguishes
  positive activity, explicit zero and missing information. Neither absent
  positive signals nor missing information confirms an outage. It uses
  `recent_heartbeat.samples`, not legacy `recent_gaps` summaries.
- `diagnostics.html` (Diagnostics) defaults to a continuous seven-day timeline
  at six-hour granularity with the same controls. Activity uses a line;
  error and stop reports use discrete bars. Exact hourly evidence, selected
  observation timestamps, coverage and provenance remain expandable.
- `usage.html` (Infrastructure & Usage) shows latest returned standard-storage
  total, first comparable returned total and their net change. Daily storage
  totals are never summed as additions. Its daily line has no hourly controls.
  Object counts provide dated context. Execution runs, execution duration and
  incoming log bytes share range controls, defaulting to seven days/six hours.
  Publication and SNS activity remain in details. No public financial estimates,
  allowance indicators or financial methodology are rendered; legacy financial
  fields remain unchanged in the published snapshot.

Charts are full width and vertically stacked. Count axes have integer ticks and
an axis label. Full timestamps use `HH:mm:ss YYYY-MM-DD UTC`; compact axes retain
UTC time and date. Hover or tap inspects intervals directly; there are no sliders
or claims of keyboard chart selection. Native expandable exact-value tables give
keyboard and assistive-technology users the same evidence. Controls, navigation
and details retain visible keyboard focus.

Compatible returned measurements are summed within selected intervals. Missing
samples remain missing: all-missing intervals are unavailable, and incomplete
subtotals have explicit sample counts and dotted outlines. Line segments connect
only consecutive complete intervals; missing or partial intervals break the line.
Measured zero retains a white baseline. Storage samples remain measured totals.
Byte units are binary and execution duration is shown in seconds, with exact
source units retained in tables. Counts do not establish successful collections,
complete coverage or a success rate. Brief evidence warnings stay visible while
technical methodology is expandable. Relative routes and snapshot URLs work
under the same site base path.

Retrieval errors are isolated by source. A snapshot can be valid and partial;
`available` means the queries/selected sources were available, **not** complete
historical coverage. Even an exhausted log query or successful metric query can
omit delayed, missing, duplicated, expired or never-emitted records. Published
coverage labels preserve that uncertainty. UTC publication older than two hours
is labelled stale, independently of observation retrieval and provider source/export
ages. A missing or invalid publication timestamp displays unavailable without an
age; a timestamp more than one minute ahead of the device clock displays an explicit
clock discrepancy, not a current publication or an unavailable timestamp. Neither
state implies collection health or schedule configuration.
`as_of_utc` records the retrieval cutoff; `published_at_utc` is set immediately
before the successful atomic publication attempt, independently of that cutoff.
The browser checks publication age every minute without additional network calls.
It fetches the JSON once per page load, with caching revalidation, and never
queries AWS. Reload obtains a newer publication.

S3 publication is one atomic conditional PUT: initial `IfNoneMatch=*`, later
`IfMatch` on the previous ETag. An older overlapping invocation cannot replace
a snapshot with a newer retrieval cutoff. Without `ListBucket`, a missing object
can return `AccessDenied`/403 on GET. That ambiguous denial permits only a
create-only PUT with `IfNoneMatch=*`; it is not proof the object is absent.
That create-only attempt still fails visibly on an existing object, denied write
or concurrent creation. Other read failures, write conflicts and serialization
or size failures leave the previous snapshot intact. A lost PUT acknowledgement has an
uncertain outcome: S3 contains either the previous object or the new complete
valid snapshot, never a partially written JSON object. No delete or rollback of
canonical evidence is attempted.

Publication failures log only allowlisted AWS error codes or fixed error
categories. Unknown codes and raw exception messages are suppressed, and the
invocation still fails with `dashboard publication failed`.

## Retrieval bounds and provenance

| Source | Maximum work per invocation | Meaning and limits |
| --- | --- | --- |
| CloudWatch `GetMetricStatistics` | 16 metrics over seven days, plus one three-hour minute heartbeat query: 17 calls total, no pagination | Completed UTC hours, 168 samples per hourly series; S3 uses seven completed daily slots. Missing samples are null. Sum of returned samples is a derived subtotal, never an exact period total. |
| Collector logs `FilterLogEvents` | Two queries (`persisted acquisition_status=success`, `REPORT`), each two-hour window, at most two pages of 100 events | At most four calls / 400 returned events; ascending service order, so a capped query may miss the newest events. Pagination truncation is explicit. No Insights scans or older log scans. |
| Normalized evidence | At most three exact keys obtained from validated persistence UUIDs in those logs; 1 MiB maximum each | Read only logged `evidence/live-v1/.../attempt.json` keys. Validate the primary versioned contract, acquisition success, complete/partial export validation, lineage and matching identity. Country timestamps remain per-country; the public global source timestamp is unknown, not a chosen country's freshness. First valid newest-log candidate supplies freshness, not a guarantee of latest evidence. No bucket listing, raw reads, date/archive scanning or polling-control access. |
| Previous public JSON | One GET, at most 192 KiB + overflow byte | Used only for ordering and conditional publication; invalid/oversized previous state fails closed. |
| Publication | One PUT; JSON at most 192 KiB | Fixed key, no archive; five separately uploaded static assets. |

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
allowance consumption and actual bills remain unavailable. The public UI presents
measured resource trends rather than financial estimates.

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
on 2026-10-10. Only one ≤192 KiB JSON and five small
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

## Offline validation and production updates

For frontend-only work, run `node tests/test_dashboard_frontend.cjs` and
`git diff --check`; publisher packaging, SAM and unrelated cloud checks are not
required when those files are unchanged.

### Local visual review

From the repository root in PowerShell, prepare an ignored preview directory:

```powershell
New-Item -ItemType Directory -Force .aws-sam/dashboard-preview | Out-Null
Copy-Item dashboard/* .aws-sam/dashboard-preview/
Copy-Item tests/fixtures/dashboard-preview.json .aws-sam/dashboard-preview/data.json
New-Item -ItemType Directory -Force .aws-sam/dashboard-unavailable | Out-Null
Copy-Item dashboard/* .aws-sam/dashboard-unavailable/
Copy-Item tests/fixtures/dashboard-unavailable.json .aws-sam/dashboard-unavailable/data.json
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand python @('-m', 'http.server', '8030', '--bind', '127.0.0.1', '--directory', '.aws-sam')
```

Open `http://127.0.0.1:8030/dashboard-preview/index.html` and
`http://127.0.0.1:8030/dashboard-preview/diagnostics.html`, and
`http://127.0.0.1:8030/dashboard-preview/usage.html`. Review all three at desktop
and narrow widths (for example 1440 and 390 pixels). Follow navigation, load each
route directly and refresh. Hover/tap chart intervals; change Period and Interval selectors and verify
related charts update together. S3 storage must remain daily. Inspect exact-value
tables for measured zero, missing and partial periods. Use Tab/Enter to open source details and
exact evidence; focus should remain visible. Stop the server with
Ctrl+C. The fixture is synthetic: 168 hourly slots, seven daily slots, unknown
and zero samples, alternating zero/missing intervals (including an isolated
missing minute), error-hour timing, variable storage/log consumption
and partial log pagination despite source
availability. Its fixed timestamps deliberately become stale; it is not live
collection evidence. The same three routes under `/dashboard-unavailable/`
provide unavailable-source previews. To review missing/network failure, temporarily rename
the preview's `data.json` and reload. Never upload this fixture to production.

Independent visual review and production deployment remain separate steps.

### Publisher and infrastructure validation

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

After separate authorization, distinguish a static frontend update from an
infrastructure or publisher update. A frontend-only update uploads reviewed static
assets to the existing dashboard stack; it does not require recreating the stack,
invoking the publisher, changing IAM or changing scheduling. For a separately
authorized initial bootstrap, package this template independently and create a
dashboard stack in eu-north-1 with ScheduleState DISABLED, using the collector
stack's evidence bucket, function, log group, metric namespace and SNS **topic name**
(not its ARN). For infrastructure updates, inspect existing resources, parameters
and scheduling before preparing a change set. Do not update the collector stack.
Upload `dashboard/index.html`, `diagnostics.html`, `usage.html`, `styles.css`,
and `app.js` only to corresponding
`public/` keys with correct MIME types and five-minute cache control. Do not upload
repository source or evidence. Verify all three direct routes, navigation,
snapshot loading and cache behavior after an authorized update. Initial bootstrap
also requires authorized publisher invocation and verification of private S3 /
HTTPS CloudFront access.
Enable this stack's hourly rule only after separate authorization for activation.
Offline frontend checks do not establish deployed revision, live permissions,
telemetry availability, caching or provider behavior. Those need separately
authorized verification; no deployment state is inferred from local files.

If publication stops, retain the previous JSON and show stale publication after
two hours. Operators use private publisher logs and existing Lambda metrics;
there are no additional alarms. Disable only the dashboard hourly rule to pause
publication. The retained hosting bucket needs separate intentional cleanup if
the stack is removed; never clean up the evidence bucket. Traffic can incur costs
even when publication is paused. Independent security/infrastructure review is
required before production deployment.
