# Public reporting: operations and stock

## Player stock page (Issue #75)

`stock.html` is the player-facing stock entry point on the existing
static site. Its production source is `frontend/stock.html` and `frontend/src/`,
built with the approved React, TypeScript and Vite exception in
[Issue #75](https://github.com/epmelito/TornRedeye/issues/75#issuecomment-6457935732).
Operations, Diagnostics and Infrastructure & Usage retain their
routes and telemetry contract below. This stock implementation is local software;
its production publication and event wiring are not activated by this document.

`stock_publisher.py` builds `public/stock.json` from one exact normalized attempt
and the previous public stock snapshot. The existing publisher handler routes S3
ObjectCreated events to this operation without running the hourly telemetry
queries. Collector acquisition and persistence never call the public publisher.
Production wiring and permissions require the separate
[stock activation review](deployment.md#stock-publication-activation-boundary).

The version-1 `stock_snapshot` allowlist contains publication/retrieval/export
times, complete/partial/failed/unavailable status, country keys and per-country
source times, and item ID/name/reported presence/quantity/shop price. There are no
evidence keys, bucket names, UUIDs, hashes, raw diagnostics, acquisition details or
credentials. Item IDs and country keys identify public stock selections, not
private collection identities. Names are rendered as text. Prices are source
shop costs in Torn dollars; observed zero remains zero.

Only explicitly returned item fields supply quantities/prices. Items or countries
missing from a later report remain visible from the previous public inventory
with unknown values, never inferred zero. Their retained names are last-reported
labels. A failed acquisition or unusable export publishes unknown values and a
visible status, rather than refreshing old quantities. Previously unseen omitted
items cannot be enumerated from a partial export. The page distinguishes a country
omitted from the snapshot from a reported country whose source time is unknown.
Empty arrays do not prove that no shop items exist. Country/source freshness,
collection age and publication age
remain separate; five-minute source-age and two-minute update warnings are display
cues, not evidence-validity or forecasting rules.

Comparisons describe net quantity/price changes between adjacent published reports
with advancing country source times. Both source and retrieval endpoints are
retained. Matching cached reports retain the last nonzero supported change and its
original intervals, labelled as the last change on the same source report, not a
new change. Same-source contradictions and regressions remain flagged across
subsequent cached reports. Conflicts, regressions and missing values block
the affected comparison. Reports skipped by publication failure and gaps may hide
changes: these are not sales counts, confirmed restocks or exact event times.
No historical archive scan is required per update or visitor.

The browser fetches only relative `stock.json` on load and schedules the next check
30 seconds after each completed refresh while visible, with an eight-second
request timeout, one outstanding fetch, and immediate rechecking on return to the
tab. Failed or invalid updates retain the last received
snapshot with an error notice and aging timestamps. An initial failure displays
unavailable. Query-based item/country, search, sort, availability and section
selections survive refresh without SPA rewrite rules; legacy country hash links
are accepted. Relative assets and JSON resolve both at the CloudFront root and
under `/public/`. There are no visitor AWS credentials, SDK calls or WebSockets.
The existing publication/collection/source-age cues remain independent.

The approved Apex Eye SVG, crimson RED wordmark and dark purple visual system
are implemented only in the player frontend. Item-first comparisons cover the
11 known destinations plus any additional returned country keys; country-first
browsing uses a compact shop list. Missing items never establish a sale location
or observed zero. Contextual details preserve net-change intervals and cached,
conflicting or regressed source classifications. Mobile replaces the table with
readable disclosures, a destination dialog and section navigation. Saved item
IDs are local to the browser; no account or service receives them.

The observation panel displays only a current quantity and, when supported, the
prior quantity recovered from the snapshot's net change over its original source
interval. It shows two endpoints, not a continuous historical curve or archive.
A missing comparison produces an unavailable state. Forecast issuances, estimates,
reliability and outcome comparison remain unavailable pending separate Issue #68
integration. No historical publication schema or infrastructure was added.

### Build and local player preview

Use Node 24 or newer and the lockfile in `frontend/`. Run
`npm --prefix frontend ci`, `npm --prefix frontend run build`, then
`python -m tools.package_stock_frontend`. Vite emits `frontend/dist/stock.html`
and external hashed `stock-assets/` files using a relative base. The assembler
writes `.aws-sam/stock-site/`, overlaying the new stock page beside byte-for-byte
copies of the operations pages, shared operations CSS and operations JavaScript.
The existing default root, `/public` origin, CSP, publisher and snapshot key do
not change. The self-hosted Roboto Condensed font ships with its OFL license.
There are no CDN resources, inline scripts/styles, router or additional UI library.

`dashboard/stock.html` and `dashboard/stock.js` are retained as the pre-React
implementation and regression reference; they are not the new production upload
target. Do not upload the raw `frontend/` source or the retained legacy stock page.
Only the assembled stock entry and its allowlisted assets are player release input.

`python -m tools.preview_stock_frontend` serves the assembled site on
`http://127.0.0.1:8767/stock.html` with the checked-in CSP. It also maps
`/public/stock.html` for base-path inspection. Without a snapshot it deliberately
shows unavailable stock. A separately selected public snapshot can be supplied
with `--snapshot`; `--label` is mandatory for such previews and appears prominently
in the page. Use an explicit archived/synthetic label; source and collection times
must retain their actual meaning. Preview input is held in memory and is never
included by static packaging. No cloud/source request is made by these tools.

Run `npm --prefix frontend test` for the actual React data/polling core and
`npm --prefix frontend run build` for type/build checks. Keep the existing legacy
frontend checks and operations checks; they preserve the prior work. Inspect the
assembled output in a real browser at desktop/mobile sizes, including filtering,
keyboard controls, error states, refresh and both URL base paths. A passing build
or fake-DOM check does not establish rendered quality. Static release remains
separately authorized.

Publication sets `Cache-Control: public,max-age=15`. FREE mode uses the managed
policy described in the [cache contract](#cloudfront-free-candidate-compatibility);
the custom MinTTL 0, DefaultTTL 300 and MaxTTL 3600 policy is historical/noncandidate
configuration. Explicit origin metadata controls stock and operations freshness.
Browser `no-cache` requests revalidation; it does not bypass a still-fresh shared
CDN object. Query parameters are excluded from the cache key, so cache-busting
URLs are not used.
A controlled fake-clock test measures 44 seconds from publication to display when
another visitor fills the shared cache immediately before publication. The ideal
15-second cache plus 30-second polling bound is 45 seconds, plus request/processing
time, for a visible tab. This is not a measured production SLA; background tabs,
network failures, notification delivery and source latency can add delay.

Each distinct notification needs at most one normalized GET (1 MiB bound), one
previous-public GET (256 KiB bound), and one conditional PUT (256 KiB bound).
There is no LIST or telemetry request. Exact configured bucket/key, primary
lineage, collection identity/retrieval partition and persisted SHA-256 metadata
are checked. Up to ten event records are accepted per invocation. Duplicate or
older retrievals do not refresh publication time. ETag conditions prevent an
overlapping older writer from replacing a newer snapshot; conflicts fail visibly.
A denied previous-public GET permits only a create-only PUT. Read/serialization/
size/write failures retain the previous object; lost PUT acknowledgements leave
the outcome uncertain. No evidence is changed or deleted.

At an illustrative 44,640 notifications/month: at most 89,280 GETs and 44,640 PUTs
before duplicates/retries, about $0.26 using the repository's historical Stockholm
reference rates ($0.0004/1,000 GET; $0.005/1,000 PUT). SDK attempts are capped at two
per operation. At 128 MiB, an assumed 0.2–2 billed seconds/invocation adds roughly
$0.03–$0.20 Lambda request/compute charges at the recorded reference rates. Those
durations are scenarios, not measured AWS runtime. Viewer requests, data transfer,
logs, storage, retries and backup are excluded; current rates/allowances need
preflight verification. A visible visitor makes about 120 snapshot checks/hour;
shared edge caching reduces origin traffic. ACTIVE FREE coverage includes eligible
viewer delivery; origin and unrelated AWS costs remain variable. See the
[coverage and residual-cost assessment](aws-cost-protection.md#hosting-options-and-cost-exposure).

The observed-stock schema is deliberately independent of future forecast records.
Issue #68 owns prospective measurement, probability semantics, prediction freshness
and measured reliability. Later validated forecasts can join on country/item at
presentation time through a separately approved contract. No forecast fields or
confidence ratings are reserved or fabricated now.

Offline checks: `python -B -m unittest discover -s tests -p test_stock_publisher.py`
and `node tests/test_stock_frontend.cjs`. The compact public fixture is synthetic;
`tests/test_stock_publisher.py:preview_fixture()` supplies a synthetic 227-item,
11-country layout for larger local visual checks. Genuine retained evidence may
be sanitized through `stock_publisher.build_snapshot` for an explicitly archived
local preview; preserve its source/retrieval times, use the actual local generation
time for publication, and never include private identities or evidence in the
build. No new source requests or private evidence are needed for normal tests.

## Operations dashboard contract

Issue [#11](https://github.com/epmelito/TornRedeye/issues/11) and
[approved decisions](https://github.com/epmelito/TornRedeye/issues/11#issuecomment-6102066164)
define the bounded publisher contract. `dashboard-template.json` describes the
separate dashboard stack; frontend changes do not require a collector stack update.
This document describes behavior and operating procedures, not the current deployed
revision or live scheduler state. Production changes require separate authorization.
See
[monitoring.md](monitoring.md) for collector signal and consumption semantics.

## Architecture and publication

The hourly operations branch of the publisher Lambda (Python 3.13, runtime boto3,
128 MiB x86, 90-second timeout) publishes only `public/data.json`. The same Lambda
routes S3 notifications to the separate stock-only publication operation. The
EventBridge schedule defaults to disabled; activation requires separate
authorization. The public snapshot contains no scheduler configuration or live schedule status.
The unversioned private encrypted S3 bucket also holds the static page assets.
The operations path introduces no hourly archive, database, frontend framework,
new custom metrics, alarms, provider requests, billing API, or visitor AWS calls.
An EventBridge rule starts disabled; automatic delivery and
asynchronous execution retries are disabled. No reserved concurrency consumes
the existing low account quota.

The template uses the CloudFront default domain, HTTPS-only viewer access,
a regional S3 REST origin, and OAC with `always` SigV4 signing. Its existing
FreePlanCandidate condition selects PriceClass_All in FREE mode and preserves
historical PriceClass_100 outside that mode. The operator-verified October 31
state is ACTIVE FREE with disabled delivery; the subscription was created in
the console, outside CloudFormation. See [verified hosting state](deployment.md#verified-hosting-state-and-ownership).
The bucket blocks public access and allows CloudFront read access only
to `public/*` for this exact distribution ARN. TLS is required for all S3 access.
The origin has no website endpoint or public ACL. No access logs are enabled
(no additional log storage or visitor information). Successful-response security headers
restrict scripts, styles and requests to the same origin, forbid frames and
disable MIME sniffing; origin 4xx/5xx retain the documented
[compatibility limitation](#cloudfront-free-candidate-compatibility). Only GET/HEAD
are served. Operations objects use explicit five-minute cache metadata; stock
objects follow the separate cache contract. No cookies or query parameters are
forwarded. No invalidation on hourly updates.

Every public field is constructed from a fixed allowlist. The operations snapshot
exposes only validated numeric telemetry and UTC timestamps from normalized
evidence; AWS responses,
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
on 2026-10-10. The hourly estimates above cover operations publication and its
one ≤192 KiB JSON and five small static assets. Stock publication adds a separate
snapshot and the assembled stock page and assets, including retained hashed assets;
these are outside those hourly estimates. Eligible CloudFront FREE delivery is
included under ACTIVE coverage. S3 origin reads and writes, Lambda execution,
storage, log ingestion and unrelated AWS services remain potentially billable,
subject to actual usage and account allowances. Origin reads depend on traffic
and cache misses; FREE delivery does not cap these other costs. Collector estimates
and their skip/retry uncertainty are unchanged; see monitoring.md. These dated
reference rates are historical assumptions, not a current bill or cost guarantee.
No visitor-volume forecast or archive-storage estimate is used.

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

### CloudFront Free candidate compatibility

The operator-verified **2026-10-31** state is recorded in
[deployment](deployment.md#verified-hosting-state-and-ownership): ACTIVE console-owned
FREE subscription, existing WAF, and disabled delivery. The managed
cache/header policies and viewer-response Function remain correct. The local
PriceClass now follows the same existing condition. Template defaults still
serve historical/noncandidate setup; preserve the actual live parameter values
on any future authorized update rather than applying defaults blindly.

The byte-match WAF path rule was removed locally after the October 30 tier
rejection and is now verified removed live. Removal loses COUNT-only diagnostics,
not enforced blocking. The local ACL still defaults to ALLOW and its IP rate
rule to COUNT; COUNT does not reduce origin requests or residual costs.
Custom policy resources remain in the template but are unattached in FREE mode.
Reverting to those incompatible policies or detaching WAF while FREE remains
active is not an authorized rollback. Subscription creation/import is not needed
and is not part of recovery. Keep delivery disabled pending the error-response
security and source-redistribution gates. No collector, publication permissions,
notification wiring, schedules, bucket policies or operations root change here.
See [rollback boundary](deployment.md#failure-and-rollback-boundary).

Managed cache TTLs are 1 / 86400 / 31536000 seconds, compared with the historical
0 / 300 / 3600. Query strings and cookies remain excluded; compressed variants
remain supported. Explicit origin metadata is mandatory for **every served object**:

| Object | Origin Cache-Control | Expected successful-response edge TTL |
| --- | --- | --- |
| `stock.json` | `public,max-age=15` (existing publisher) | 15 seconds |
| `data.json` | `public,max-age=300` (existing publisher) | 300 seconds |
| Five operations assets and font license | `public,max-age=300` | 300 seconds |
| `stock.html` | `public,max-age=0,must-revalidate` | Minimum 1 second; browser revalidates |
| Hashed stock assets | `public,max-age=31536000,immutable` | Up to one year; retain old hashes |

The packager now emits `stock-site-upload-metadata.json`, keyed by exactly the
existing upload allowlist, with `CacheControl` and `ContentType` for each static
object. Apply those as real S3 object properties, not `x-amz-meta-*` user metadata.
Neither manifest is public content. Existing JSON metadata must be verified
separately; static packaging does not publish JSON or repair deployed metadata.
Missing Cache-Control can cache for a day, and an excessive value is no longer
capped at one hour. Never rely on viewer request `no-cache` or a query suffix to
bypass CloudFront. The one-second floor also applies to `no-cache`, `no-store`
and `private`; only explicitly public content belongs behind this behavior.
[Managed cache settings](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html).

`CachingDisabled` preserves zero caching but increases origin requests.
`UseOriginCacheControlHeaders` has a zero minimum/default but includes viewer
Host, other headers and all cookies in the cache key/forwarding; it is not an
equivalent private S3/OAC replacement. The candidate uses CachingOptimized with
explicit metadata. No origin request policy or extra cache behavior is added.

The frontend still polls 30 seconds **after completion** of the last request,
uses relative `stock.json`, retains the last valid snapshot on failure and keeps
source timestamps distinct. The local cache model exercises replacement phases
and another viewer refilling an edge: under immediate successful requests,
15-second edge freshness plus polling gives at most 45 seconds after publication.
This is a model, not measured CloudFront latency or an acquisition freshness SLA.
Request duration (up to the existing eight-second timeout), hidden tabs, failed
requests, edge propagation/revalidation and stale-on-error behavior can extend
it. Degraded YATA remains stale independently of publication/cache freshness.
Error caching is separate from object TTL and is unchanged by this candidate.

The managed response policy runs before the viewer-response Function. The Function
overwrites CSP, framing, referrer policy, HSTS and nosniff with the historical custom
policy values, while preserving status, body, ETag, cache metadata and cookies.
It has no logging, network, key-value store or shared distribution association.
It removes the managed policy's legacy `X-XSS-Protection` addition on invoked
responses, preserving its absence in the existing policy. Errors still have the
managed fallback, including that extra header.
[Policy ordering](https://aws.amazon.com/blogs/networking-and-content-delivery/amazon-cloudfront-introduces-response-headers-policies/),
[managed headers](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-response-headers-policies.html).

| Response path | Candidate security result | Activation status |
| --- | --- | --- |
| Origin/cache 2xx, HEAD, 3xx including viewer conditional response | Function restores the five existing header values when invoked | Offline code verified; AWS invocation/order still needs verification |
| Origin 4xx/5xx, including S3 missing object 403/404 | Viewer-response Function is skipped; managed fallback has no CSP, SAMEORIGIN rather than DENY, and strict-origin-when-cross-origin rather than no-referrer | **Blocking incompatibility** |
| WAF BLOCK 403 | Generated before normal origin response processing; no equivalent header guarantee; custom WAF responses are unavailable on Free | **Blocking incompatibility before enforcement** |
| CloudFront-generated HTTP denial, malformed request, edge/Function failure | Not proven to receive the current or candidate policy on every path | Missing live evidence; do not claim universal error coverage |

[AWS's origin error restriction](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/edge-function-restrictions-all.html)
prevents equivalence with this composition. S3 object user metadata cannot supply
arbitrary response security headers, and HTML meta CSP cannot replace the full
header contract. Custom error pages do not establish that the Function will run;
do not turn errors into 200 responses. Lambda@Edge would add a separately billed
execution path and does not solve every edge/WAF-generated error; it is outside
this package. Keep delivery disabled until an independently reviewed, explicitly
approved bounded difference or an equivalent Free-supported solution resolves the
gap. Retaining historical custom policy resources does not authorize reattaching
them to the active FREE distribution. Offline tests model bypass; they do not
emulate AWS errors.

#### Error security comparison and proposed exception

For origin errors, the historical custom-policy contract overrides all five
headers. It is the comparison baseline, not the currently associated policy.
The FREE configuration cannot reproduce that behavior using any managed response
policy: AWS's published managed policies contain no CSP. Viewer-request Functions
could generate selected rejection responses with headers, but cannot intercept
later S3 failures. S3 user metadata is not an arbitrary HTTP security-header
mechanism. Custom error pages do not remove the origin-error invocation restriction;
rewriting status to 200 also breaks error semantics. WAF custom responses are not
included on Free, and Lambda@Edge adds separately billed execution. No complete
Free-supported equivalent was identified within the existing S3/OAC architecture.
This conclusion does not establish the behavior of every CloudFront-generated error.

| Header on origin 4xx/5xx | Historical custom-policy contract | FREE fallback without origin security headers | Security significance |
| --- | --- | --- | --- |
| CSP | Full existing restrictive CSP including `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'` | Absent | Removes script/resource/base/object restrictions and CSP framing prohibition from the error document. Material loss of defense if an error ever becomes active or attacker-influenced HTML. |
| X-Frame-Options | DENY | SAMEORIGIN | Still rejects cross-origin framing, but permits same-origin framing. CSP no longer supplies the stricter independent prohibition. |
| Referrer-Policy | no-referrer | strict-origin-when-cross-origin | May disclose the full referring URL on same-origin requests and the origin on cross-origin HTTPS requests; suppresses HTTPS-to-HTTP downgrade referrers. Broader disclosure than sending none. |
| HSTS / nosniff | max-age=31536000 / nosniff, forced override | Same expected values; managed HSTS does not override an origin value | No expected reduction for ordinary S3 errors with no supplied HSTS. Verify actual values; an unexpected origin value is outside the proposed exception. |
| X-XSS-Protection | Not added | `1; mode=block` added by managed policy | Legacy browser behavior differs; no substitute for CSP and no modern XSS protection claim. |

**Proposed exception EX75-ERROR, not approved:** permit only the table's missing
CSP, SAMEORIGIN, strict-origin-when-cross-origin and additional legacy XSS header
on genuine origin 4xx/5xx and observed default WAF BLOCK 403 error documents of
this one distribution. Preserve actual HTTP error status. Successful content,
GET/HEAD, redirects and conditional responses when the Function runs retain the
existing five values and absence of the legacy XSS header. HSTS/nosniff must
remain unchanged. Do not generalize acceptance to arbitrary edge/Function errors.

Before any candidate delivery, the owner must approve this exact proposed matrix
and explicitly authorize a bounded verification-only window after ACTIVE Free
coverage. This is a security decision for testing, not production acceptance.
For final acceptance, compare retained historical header evidence where available
and the configured baseline with fresh FREE-response headers, Content-Type and
sanitized bodies for missing-object 403/404 and available safe
5xx cases; inspect a default WAF block separately; confirm error bodies have no
active content, executable reflection, sensitive URLs, credentials or private
evidence; and confirm paths still use private OAC/public-only content. Error
format changes, active/custom HTML error pages, any weaker HSTS/nosniff, sensitive
URL parameters or an unclassified response path invalidate the exception and
require delivery disabled plus renewed review. If a safe 5xx cannot be observed,
that portion remains unverified and is not covered by a blanket approval.
Record the approved status/body/header matrix and distribution identity explicitly.
If candidate response captures require the verification window, identify those
missing captures in the provisional decision first; disable delivery again at
the window's end unless all required evidence and final owner acceptance exist.
Without that owner decision, retain the existing FREE-compatible configuration
and keep public delivery disabled. Do not revert the enabled candidate parameter
or reattach incompatible custom policies. No exception has been accepted by this
implementation.

#### Deterministic origin metadata delivery

`python -m tools.plan_public_metadata` is a local dry-run planner with **no S3
client or write mode**. Supply the assembled directory and a private JSON inventory
with `bucket`, `prefix: "public/"`, `complete: true`, and `objects` containing
`Key`, opaque quoted `ETag`, `Size`, `CacheControl` and `ContentType` from S3.
The inventory must include every page of a `public/` listing and HEAD results
for every object, captured only after later read authorization. Offline validation
cannot prove that `complete` is truthful. Stop publishers during capture/application
to avoid inventory races; never stop or modify the collector. Both public JSON
objects must exist; missing snapshots are an activation blocker, never invented.

The planner reads both generated manifests, validates exact matching key sets and
cache/MIME values, hashes every assembled file and emits operations in sorted-key
order. Assembled files get conditional PUT plans; existing objects require IfMatch,
new objects IfNoneMatch `*`. Existing public JSON and retained hashed assets get
repair or verification plans. Every existing object must be a known operations
file, `stock.html`, one of the two JSONs, the font license, or a flat hashed
stock asset with an approved extension explicitly listed in the current or a
reviewed retained-release metadata manifest. Filename shape alone is insufficient.
Pass prior reviewed release metadata using repeatable `--retained-manifest` options;
do not construct an allowlist from unknown inventory names solely to bypass rejection.
Unknown keys, private paths, folder markers, encoded/nested/traversal names,
unhashed assets, duplicates, modified
manifest values and either manifest as public content fail closed. Existing legacy
assets outside this contract require explicit inventory review; do not silently
delete, exclude or block assets still referenced by cached pages.

Example local invocation, with separately captured inventory; this performs no
cloud operation and writes the plan outside the assembled upload directory:

```powershell
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand python @('-m', 'tools.plan_public_metadata', '--directory', '.aws-sam/stock-site', '--inventory', '.aws-sam/public-inventory.json', '--retained-manifest', '.aws-sam/retained-release-metadata.json', '--output', '.aws-sam/public-metadata-plan.json')
```

Review the plan's exact bucket, complete expected key set, source SHA-256/size,
opaque ETags, actions and PUT `CacheControl`/`ContentType` before authorizing any
application. The planner is **preparation**, not an upload executor. Authorized
application must follow this contract:

1. Keep delivery disabled. Pause only public-writing triggers/invocations, wait
   for in-flight publisher writes, capture schedules/notifications and refresh
   inventory. Confirm the bucket is the private hosting bucket and current key
   set equals inventory. Do not use bucket sync, deletion or a recursive upload.
2. Rehash local sources against the plan. For every existing write, GET using
   IfMatch, verify ETag/size and save body SHA-256; capture object tags under the
   writer pause. For byte-preserving metadata repairs, preserve user Metadata,
   Content-Encoding, Content-Disposition, Content-Language, Expires, object tags,
   storage class and encryption properties while replacing **only** Cache-Control
   and Content-Type. For assembled uploads, representation headers must describe
   the reviewed local bytes: do not inherit an old Content-Encoding that describes
   a different body. Reconcile differing properties explicitly before execution;
   the manifest specifies CacheControl and ContentType, not every object property.
   If an unexpected encryption, retention/lock, versioning or other property
   cannot be preserved,
   stop for review. Do not guess defaults or publish private metadata. Metadata
   repair writes back the exact fetched bytes; an assembled upload uses its
   reviewed local bytes. Use conditional PUT, not an unconditional self-copy that
   could overwrite a newer JSON. Permissions/tags and conditional failure handling
   must be reviewed before execution; no executor is supplied here.
3. Apply only the plan's per-key conditional requests. Any 412, missing object,
   size/hash mismatch, changed key set or uncertain write stops the entire run.
   Read back the uncertain key and reconcile receipts; never retry unconditionally.
   Retain the partial-operation journal and original properties/bodies privately
   for recovery. No canonical evidence or source data is written.
4. Post-upload, list every `public/` page again and HEAD every expected key.
   Require exact key-set equality with inventory union assembled keys, exact
   planned Cache-Control/Content-Type, preserved properties/tags or explicitly
   reviewed upload differences, and correct sizes. GET changed keys: require assembled SHA-256 or the pre-repair
   body SHA-256 respectively. ETag is a concurrency token, not a content digest.
   Confirm no manifest was published. Reinventory and verify resumed publisher
   output still uses the existing 15/300-second JSON metadata. Do not enable
   CloudFront until these checks and the separate Free/security gates pass.

This covers retained old hashes as well as newly assembled files. It does not
prove live metadata delivery, change publisher metadata, or repair cache entries
already held at edge/browser caches. Later verification must account for previous
TTL expiry or a separately authorized bounded invalidation.

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
node tests/test_stock_frontend.cjs
npm --prefix frontend test
npm --prefix frontend run build
python -m tools.package_stock_frontend
sam validate --lint --template-file dashboard-template.json --region eu-north-1
sam validate --lint --template-file cloudfront-waf-template.json --region us-east-1
node tests/test_cloudfront_headers.cjs
git diff --check
```

Historical validation on **2026-10-29**: isolated cfn-lint 1.59.0 passed the
dashboard, WAF and then-proposed subscription template. The subscription also
passed with the published `us-east-1` provider schema (Last-Modified
2026-10-29 19:29:18 UTC; SHA-256
`c638c50992e98eb0047ec99ce400d806ed3735c8adb203f8a71f0d3ec914c324`).
An invalid PlanFamily negative control failed E3030 as expected; bundled SAM
subscription lint failed E3006 without suppression. These are dated local
validation results, not evidence that CloudFormation created the subscription.

As of **2026-10-31**, the subscription exists outside CloudFormation and its
unused uncommitted creation template and exclusive test have been removed.
No runtime or recovery code depended on that file. Do not recreate or import the
console subscription. Prior schema/creation validation does not add a current
registry/import requirement or block reconciliation of the remaining templates.
The private earlier review bundles remain historical evidence, not deployment
packages for the active console-owned subscription.

For the remaining templates, the existing isolated local validator can be used
offline; no subscription template, schema download or AWS call is required:

```powershell
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand .aws-sam/cfn-lint-venv/Scripts/cfn-lint.exe @('-t', 'dashboard-template.json', '-r', 'eu-north-1')
Invoke-NativeCommand .aws-sam/cfn-lint-venv/Scripts/cfn-lint.exe @('-t', 'cloudfront-waf-template.json', '-r', 'us-east-1')
```

The generated `.aws-sam/dashboard-publisher.zip` contains `dashboard_publisher.py`,
`stock_publisher.py`, `tornredeye/__init__.py` and `tornredeye/evidence_paths.py`, using deterministic
atomic packaging. Boto3/botocore
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
Use the assembled `.aws-sam/stock-site/` output, not raw source, for a reviewed
frontend release. Its generated `stock-site-files.json` lists the operations files,
`stock.html` and external hashed `stock-assets/` files (including font/license).
Upload only those listed paths to corresponding `public/` keys, applying the generated
upload metadata as object properties. Do not upload either manifest.
Retain five-minute caching for operations assets; revalidate the stock HTML
and use immutable caching for hashed stock assets. Retain older hashed assets for
cached HTML references. Do not upload the manifest or any preview JSON/evidence. Do not upload
repository source or evidence. Verify all four direct routes, navigation,
snapshot loading and cache behavior after an authorized update. Initial bootstrap
also requires authorized publisher invocation and verification of private S3 /
HTTPS CloudFront access.
Enable this stack's hourly rule only after separate authorization for activation.
Offline frontend checks do not establish deployed revision, live permissions,
telemetry availability, caching or provider behavior. Those need separately
authorized verification; no deployment state is inferred from local files.

If operations publication stops, retain the previous `data.json` and show stale
publication after two hours. The stock page has its separate two-minute update-age
cue. Operators use private publisher logs and existing Lambda metrics; there are
no additional alarms. Disabling the dashboard hourly rule pauses only operations
publication; stock notifications are independent. Follow the
[stock publication pause procedure](deployment.md#stock-publication-activation-boundary)
and the writer pause contract above when quiescing public writes. The retained
hosting bucket needs separate intentional cleanup if
the stack is removed; never clean up the evidence bucket. Traffic can incur costs
even when publication is paused. Independent security/infrastructure review is
required before production deployment.
