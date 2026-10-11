# Post-trial AWS cost protection and evidence continuity

Architecture assessment for [Issue #17](https://github.com/epmelito/TornRedeye/issues/17).
Research verified **2026-10-11**; proposals below are not approved deployments.
No resources, schedules, account plans, subscriptions or evidence were changed.

## Recommendation

First establish a verified backup outside this AWS account. Then target
**CloudFront Free flat-rate hosting**, conditional on account eligibility and
security compatibility verification. Keep the existing private S3 origin,
sanitized snapshot and bounded hourly publisher. Before any account upgrade,
disable public pay-as-you-go delivery and verify that the disable has propagated.
Keep publication disabled until separately approved.

CloudFront Free is not available to the current Free account and is not a
drop-in replacement for its custom policies. Do not upgrade simply to discover
whether migration works. Prepare the change offline, approve its prerequisites,
then perform a protected transition. If compatibility or eligibility fails,
leave public delivery disabled; assess GitHub Pages rather than buying a
larger AWS plan by default. Availability of a portfolio dashboard is secondary
to preserving evidence and avoiding uncontrolled charges.

## Evidence and current risks

**User-supplied verified AWS baseline, 2026-10-11:** region `eu-north-1`,
account plan `FREE`/`ACTIVE`, remaining credits **$139.96**, expiration
**2027-04-08T16:14:05.093000+00:00**. The collector and dashboard stacks exist;
the frontend is public through pay-as-you-go CloudFront and private S3/OAC.
Custom cache and response-header policies are in use. Publisher Lambda is
deployed but has not been intentionally invoked; its hourly rule is **DISABLED**.
This assessment did not independently inspect AWS or measure a bill/burn rate.

**Repository evidence at `c04210504fb9cf863d2427333e2b30554c3a00e4`:**

- [Dashboard contract](dashboard.md) and [dashboard template](../dashboard-template.json)
  define HTTPS, distribution-scoped `public/*` reads, blocked public S3 access,
  OAC `always`/SigV4, GET/HEAD, five-minute caching, restrictive CSP and no visitor
  AWS API calls. Publication has fixed query/byte/runtime bounds and one snapshot;
  archive size cannot increase publisher query work.
- [Collector template](../template.json), [persistence](../s3_persistence.py),
  [README](../README.md) and [recovery instructions](deployment.md) establish
  conditional evidence writes, raw-first persistence and visible partial failure.
  Both buckets have `Retain` on deletion/replacement. Evidence has no template
  expiry, versioning or Object Lock. Retention is not an independent backup.
- [Monitoring](monitoring.md) distinguishes persisted observations from HTTP
  success. Matching logs can duplicate increments; they do not count distinct
  collections or prove complete history. Logs expire after 14 days for the
  collector and seven days for the publisher.

No separate decision documents are present. Accepted issue decisions referenced
by these contracts govern existing behavior. Historical deployment statements
in those documents are not evidence of current live configuration: verify live
lifecycle, retention, permissions and collector schedule before migration.

**Material risks:** public requests can consume credits now and incur uncapped
usage charges after upgrade; cache misses also reach S3. Account closure can
make all evidence inaccessible. Stack retention does not prevent privileged
deletion, lifecycle expiration, corruption or account-level deletion. Exact
archive size, historical losses, current request volume, credit expiry details
and account-wide remaining allowances are unavailable here.

## Account transition

AWS's [account-plan documentation](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
states that Free accounts close at six months **or earlier credit exhaustion**.
Resource/data access is lost; content is retained for 90 days before permanent
deletion. A paid upgrade within that window can restore access. This is an
emergency recovery window, not a backup or automatic transition to paid service.
An ordinary voluntary upgrade allows continuing eligible credits but exposes
usage beyond credits to standard charges. Some actions, including joining AWS
Organizations or setting up Control Tower, automatically upgrade the account;
do not use them as a backup strategy.

Credit carryover is temporary: AWS's [Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/)
describes activity-credit expiry 12 months after account creation. Check each
credit's actual expiry and eligible services in Billing; do not infer an expiry
date from the six-month account deadline or assume credits cover plan purchases.

While still Free, prepare compatibility changes, export/recovery tooling,
independent backups and operator procedures; inspect existing metrics and account
credit notices. Budget availability/permissions must be confirmed. A paid-account
upgrade and flat-rate subscription require later approval and verified
eligibility; [PricingPlanManager](https://docs.aws.amazon.com/PricingPlanManager/latest/UserGuide/plans.html)
explicitly excludes Free Tier accounts.

## Hosting options and cost exposure

USD estimates exclude taxes, credits and uncertain account allowances. They are
scenarios, not current bills, traffic forecasts or account-wide ceilings.

| Approach | Approximate exposure | Limitations and judgment |
| --- | --- | --- |
| Retain CloudFront pay-as-you-go | Low traffic may fit conditional shared allowances. Illustrative Europe HTTPS: 100M requests with an otherwise unused 10M allowance cost **$108 for requests alone**. Another 1,000 chargeable GB at $0.085/GB adds **$85** in that price tier. | Existing policies work. Cache/HTTPS/OAC protect the origin and data, not the wallet. Requests and transfer remain usage-based even with cache hits. Alerts plus manual disable cannot guarantee a maximum bill. Temporary fallback only if the owner explicitly accepts this risk. |
| CloudFront Free flat-rate | **$0/month for included delivery/protection**, 1M requests and 100 GB monthly reference allowances; origin and unrelated services remain variable. | Conditional target. Requires paid-account eligibility, WAF and policy replacement. Allowances are not hard limits; excess can cause delivery degradation, not an overage invoice for included delivery. No guarantee of unrestricted performance. |
| GitHub Pages, static sanitized snapshot | Public-repository hosting is available with GitHub Free; no AWS visitor request/egress charges once CloudFront is disabled. AWS collection and any snapshot production still cost money. | Simpler public hosting. Soft 100 GB/month bandwidth and 1 GB site limits, rate limiting and acceptable-use restrictions. Begin with manual snapshot publication; hourly external publishing requires a new approved workflow, credentials/security review and Actions cost assessment. Header control and base-path behavior need verification. |

Sources: [CloudFront pay-as-you-go pricing](https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/),
[flat-rate tiers](https://docs.aws.amazon.com/PricingPlanManager/latest/UserGuide/plans.html),
[GitHub Pages availability](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
and [limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
The two pay-as-you-go calculations are independent scenario components, not an
inferred relationship between requests and bytes. Edge geography matters despite
the Stockholm origin. Random paths, eviction and revalidation can increase
origin requests; the absence of query-string forwarding is helpful but not a cap.

Separate origin scenario: **10M chargeable S3 Standard GETs would cost $4** at
the existing Stockholm reference rate $0.0004/1,000. Neither cache-hit ratio nor
charged GET volume is measured. See the [S3 regional offer](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-north-1/index.json)
referenced in dashboard.md (rates verified there on 2026-10-10). Recheck prices
and error charging before implementation.

Adding standalone pay-as-you-go WAF is not a cheap spending cap: reference
charges are $5/web ACL/month, $1/rule/month and $0.60/M requests before additional
features. One ACL/two rules processing 100M requests is **$67/month**, plus
CloudFront. Distributed traffic can bypass per-IP limits. Do not add Shield
Advanced, Bot Control, paid logging or a higher tier without demonstrated need.
[WAF pricing](https://aws.amazon.com/waf/pricing/).

## Target compatibility and security gates

The [CloudFront plan specification](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html)
confirms: Free supports OAC, managed policies, CloudFront Functions and five WAF
rules with five-minute IP rate limiting; custom cache/response policies require
Business or above. Access/WAF logs are unavailable on Free. S3 Standard storage
credit is 5 GB; requests are separate. Three Free subscriptions per account are
allowed; historical usage can affect eligibility. Blocked WAF/DDoS traffic is
excluded from allowances. Included traffic has no overage charge, but sustained
excess can degrade delivery. Other CloudWatch storage/queries and additional
metrics are excluded; Lambda@Edge and unrelated services remain chargeable.

Pro is $15/month and still lacks the custom policies; Business is $200/month.
Neither is proportionate merely to preserve two current policies. Verify the
actual subscription offer, eligible resources and exclusions before accepting it.

Recommended compatibility design, **not yet implemented or live-tested**:

1. Preserve private encrypted S3, TLS-only access, distribution-scoped OAC reads,
   HTTPS-only visitors and the existing publisher/evidence IAM boundaries. Do not
   make S3 public, expose evidence, add viewer API calls or change YATA polling.
2. Replace the custom cache policy with managed `CachingOptimized`. Its defaults
   differ (one-second minimum, one-day default, one-year maximum). Preserve
   explicit `Cache-Control: public,max-age=300` on JSON and set deliberate cache
   metadata on every static asset; otherwise freshness changes. No query strings
   or cookies enter its cache key. Verify replacement JSON becomes visible within
   the intended cache interval and stale indication still works; avoid hourly
   invalidations. [Managed cache policies](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html).
3. Use managed `SecurityHeadersPolicy` plus one distribution-exclusive viewer
   response CloudFront Function to retain the exact current CSP, `DENY` framing,
   `no-referrer`, HSTS and `nosniff` on successful content. The managed policy
   alone has **no CSP**, uses `SAMEORIGIN` and a different referrer policy.
   AWS demonstrates [security headers in Functions](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/example_cloudfront_functions_add_security_headers_section.html);
   [managed response headers](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-response-headers-policies.html)
   provide the baseline. Keep the function without network access, state or routine
   request logging. Verify plan acceptance and header ordering; this is a proposed
   supported composition, not proof of live compatibility.
4. **Error-response security is an unresolved approval gate:** viewer response
   functions do not run for origin HTTP status >=400. Check missing paths,
   blocked requests, conditional responses and normal assets. Managed headers
   remain the fallback, but exact existing CSP/framing/referrer behavior is not
   guaranteed on errors. Do not silently relax the contract or substitute an HTML
   meta CSP (which cannot replace all header directives). Either approve a bounded
   error-response difference after review, demonstrate an equivalent supported
   design, or reject this target. [Edge-function restrictions](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/edge-function-restrictions-all.html).
5. Attach a dedicated `CLOUDFRONT`-scope WAFv2 ACL in `us-east-1`; propose a
   supported exact-path restriction for the four public objects/root and an IP
   rate rule. Verify rule support/order and normal mobile clients before enforcing.
   Rate thresholds need measured baseline and owner approval; NAT/shared clients
   can be blocked and botnets are not bounded by one IP rule.

Keep regional application stacks in Stockholm. Manage global WAF and subscription
in a small `us-east-1` protection stack referencing the existing distribution;
the dashboard stack continues to own the distribution and its associations.
Avoid a second public distribution, domain/DNS service or cross-region origin.
The [PricingPlanManager API guide](https://docs.aws.amazon.com/PricingPlanManager/latest/UserGuide/getting-started-pricingplanmanager-api.html)
requires an existing ACL; it does not create one. Free cancellation is immediate.

[CloudFormation supports `AWS::PricingPlanManager::Subscription`](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-pricingplanmanager-subscription.html),
including existing distribution/ACL ARNs. Free activates automatically; paid
subscriptions require separate approval and pending subscriptions leave resources
on pay-as-you-go. Tier/usage changes cannot be made through ordinary stack updates.
Model resource ownership/dependencies explicitly, verify registry/tool support,
and reconcile any console changes into templates instead of claiming drift-free
operation. Subscription deletion/stack rollback can remove protection; disable
delivery first and retain the subscription during unrelated teardown until an
approved cancellation. Do not rely on ambiguous deletion timing as a safeguard.

## Whole-project consumption and monitoring

Use [monitoring.md](monitoring.md) and [dashboard.md](dashboard.md) as authoritative
consumption contracts; do not duplicate their estimators or present project
subtotals as account allowance remaining.

| Component | Exposure and recommended visibility |
| --- | --- |
| CloudFront/origin | Existing Requests, BytesDownloaded and 4xx/5xx error rates; Free-plan allowance notices if subscribed. S3 origin request costs remain variable; no measured request totals currently. |
| S3 | Daily bucket bytes/object counts cover growing evidence; request estimates include control reads/writes, skips, conflicts and SDK retries. Preserve complete raw and normalized retention. Artifact buckets and backup export also cost money. Flat-rate storage credit is conditional/account-wide, not a reserved archive budget. |
| Lambda | Requests and billed GB-seconds for collection/publication, including skips, waits, initialization and retries. No assumed collector average runtime. Disabled publisher has no scheduled compute; residual storage/hosting is still billable. |
| CloudWatch | Existing two custom metrics/three alarms: about $0.90/month gross at documented reference rates, plus variable log ingestion/storage/API reads. Bounded publisher queries remain independent of archive size. Retention makes full-month reconstruction from REPORT logs incomplete. |
| EventBridge | Collector uses Scheduler (14M shared monthly invocation allowance, then reference $1/M); publisher uses a legacy scheduled Rule, not Scheduler. Do not apply Scheduler allowances to that rule. No scheduled publisher deliveries while disabled; target Lambda charges are separate. Reverify applicable Rule pricing before activation. [EventBridge pricing](https://aws.amazon.com/eventbridge/pricing/). |
| SNS | Existing private notifications: variable requests/deliveries under conditional shared allowances. Keep destinations configurable; no personal addresses in code or public output. |
| Proposed protection/backup | Free plan includes associated supported WAF/edge compute; no new periodic scanner or logging service. Ordinary budget notifications are free; alarms, backup media, export GET/LIST/data transfer and any new external publishing have separate costs. |

At 744 monthly runs, the existing **assumed** publisher 5-90 second scenario is
about $0.00790-$0.13965 Lambda and $0.0040-$0.0049 S3 requests before retries,
allowances or other costs (dashboard.md; regional rates checked 2026-10-10).
This is not a timeout-based billing ceiling or a total project estimate.
No credible whole-project total is available without storage, billed duration,
traffic and account-wide usage. Account allowances and credits are conditional,
shared and time-sensitive; Free flat-rate delivery does not cap the AWS bill.

Propose two standard traffic alarms on existing Requests and BytesDownloaded,
plus existing error-rate inspection, in `us-east-1`; thresholds must be approved
against recent traffic and tolerable response delay. Prefer five-minute sums,
private operator notifications, missing data as unknown, and no extra metric
subscription or access logs. Approximate gross alarm increment is $0.20/month
at a $0.10 standard alarm reference rate, subject to regional verification and
unused account allowances. This is a proposal, not an installed alarm.
[CloudFront monitoring](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/monitoring-using-cloudwatch.html)
confirms default metrics are free, additional metrics and alarms are not.

Propose an account-wide gross-cost budget (exclude credits to reveal consumption)
and a CloudFront cost budget, with configurable actual/forecast notices. Owner
must choose USD thresholds; no number here claims an affordable spending cap.
[AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
updates up to three times daily with billing/notification delays. Usage can
exceed a threshold before an alert arrives; alerts do not stop resources.
Budget actions are not a universal CloudFront shutdown mechanism.
[Budget pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/)
allows free monitoring/notifications; emailed budget reports and some actions
have charges. Prefer notification-only budgets and an explicit operator response.

**Proposed response:** inspect account credits weekly while Free and daily during
migration/incidents, with calendar reminders independent of AWS. Escalate
unexpected burn immediately; proposed credit warnings at $50 and $20 require
owner acceptance and do not imply a safe remaining runtime. For abnormal traffic,
confirm metrics/plan state, then use separately authorized WAF blocking or disable
the distribution and wait for propagation. Capture bounded diagnostic evidence,
check S3/other-service usage and inspect billing later; incurred charges remain.
DNS removal or a private bucket alone cannot stop requests to the public
CloudFront hostname. Disabling the publisher does not stop visitor charges.
Pause collection only with approval when depletion threatens evidence continuity;
preserve halts/control state and record the resulting gap. Never delete history
to meet a budget. On paid plans, disabling a distribution does not cancel the
subscription fee; cancellation can return associated resources to pay-as-you-go.

## Controlled migration and rollback

All steps involving AWS writes, evidence export or account changes below require
their own approved implementation work; none were performed for Issue #17.

1. **Immediately after approval:** inventory account/credits and live retention,
   establish verified external backup and test recovery. Agree recurring cost,
   acceptable downtime, error-header behavior, traffic thresholds and backup RPO.
   Freeze changes that trigger an automatic paid upgrade.
2. **By 2027-02-28, or earlier if credits fall:** finish offline templates/tests
   and eligibility preflight, including historical usage, available Free slots,
   unsupported/shared resource associations and deployment tooling. A full live
   eligibility check may only succeed after upgrade; approval must accept that
   uncertainty and the shutdown fallback. Prepare operator/rollback instructions
   and refresh the backup before any irreversible transition.
3. **Explicit approval gate A:** approve a public outage and disable the current
   distribution while still Free; confirm deployed disabled state and cessation
   of delivery. Leave S3 private and publication DISABLED. Account upgrade does
   not wait for an alert to protect an enabled public endpoint.
4. **Explicit approval gate B:** approve paid-account transition and temporary
   origin/WAF costs; confirm payment/credit terms, then upgrade. Keep delivery
   disabled through incompatible-policy replacement, WAF attachment and
   subscription creation. ACL charges may apply before activation; distribution
   changes/subscription synchronization are not atomic.
5. Verify subscription **ACTIVE**, current tier **FREE**, correct distribution
   and ACL association, no pending paid/cancellation change, and distribution
   deployment completed. Inspect included/excluded usage in Billing later.
   Stack completion or a $0 estimate alone does not prove financial protection.
6. **Explicit approval gate C:** enable delivery for bounded browser/security
   checks, verify private origin denial, headers, hourly replacement/cache
   behavior and valid partial/stale JSON. Publisher invocation and hourly
   enablement require separate approval; preserve collector behavior throughout.
   If any protection/security check fails, disable delivery again.
7. **By 2027-03-15:** either complete the protected transition, select the approved
   alternative, or keep public hosting shut down and finish evidence-continuity
   preparations. Do not wait until April 8 or assume credits last until then.

Rollback preserves the external backup, evidence bucket and disabled publisher.
Retain an active Free subscription where possible; cancel/remove it only after
delivery is disabled because cancellation can restore pay-as-you-go exposure.
Do not silently re-enable pay-as-you-go, detach WAF or upgrade to Pro/Business.
Restore prior templates only with separately approved risk and ownership checks.
No reversible migration step reverses the paid-account financial obligations;
if no upgrade is approved, complete backups and plan a collection interruption
before account closure. Continuing evidence collection beyond expiry requires
an approved funded account or a separate approved collection migration.

## Evidence continuity and recovery requirements

The [evidence backup workflow](evidence-backup.md) documents the Issue #19 offline
tooling and its manifest/verification contract. It does not establish a completed
live backup or authorize export, storage, collector pauses or restoration.

Smallest credible backup: an owner-controlled encrypted export on **two
independent storage copies outside this account**, with separately recoverable
keys. Private local media is sufficient initially; a second AWS bucket in the
same account is not account-loss protection. Do not publish backups or manifests
in GitHub/dashboard output. Storage purchase, export and recurring backup cadence
need approval; no cross-account replication service is proposed.

- Export every object in both canonical prefixes, including malformed, missing,
  failure, empty-body, full foreign-market payload and raw-only partial writes.
  Do not filter to observed status, latest records or Japan Xanax stock rows.
  A normalized record without raw evidence can be legitimate. Preserve exact
  keys/bytes, ContentType and all raw provenance metadata; normalized JSON links
  include collection ID, retrieval/source/export timestamps, raw hash/length and
  original bucket/key identity. Preserve original identities across relocation.
- Use an explicitly authorized, resumable paginated inventory/export with fixed
  prefix scope, bounded page/batch size, retries, bandwidth/runtime limits and a
  preflight GET/LIST/egress estimate based on measured bytes/object counts. This
  one-time archive traversal belongs to backup work, **never** the publisher.
  GetObject returns metadata; avoid needless per-object extra queries. Do not
  assume external downloads are covered by CloudFront free transfer.
- Agree a consistent cutover: preferably approve a brief collector pause, drain
  in-flight work, record UTC gap boundaries, then inventory stable prefixes.
  If pausing is unacceptable, require a bounded reconciliation of concurrent
  writes; a single listing during collection is not a complete backup proof.
  Record manifest cutoff, every page completion, per-prefix counts/bytes and
  failures. Hash all downloaded bytes with SHA-256, verify raw links against
  normalized hashes/lengths, and flag unpaired/conflicting records without repair
  or deletion. ETags alone are not universal content hashes. Verify both backup
  copies against the manifest; unresolved transfers prevent a completeness claim.
- Preserve the exact committed collector/schema code and configuration needed
  to interpret history. Keep a private forensic copy of control/configuration
  separately, without credentials, for diagnosis; **never automatically restore
  a stale lease or clear a polling halt**. Resume only under deployment.md's
  manual recovery safeguards with schedules disabled first.
- Test restoration of representative success, failure, empty and raw-only cases
  into an approved isolated private destination. Conditional no-overwrite writes
  must compare bytes/metadata on rerun, reject conflicts and verify the restored
  manifest. Prefer exact normalized restoration. Reconstruction from raw needs
  original retrieval metadata, IDs and code, and cannot recreate missing raw
  bytes or lost failure details; mark any derived reconstruction explicitly.
  Keep original embedded bucket references with a documented location mapping,
  rather than rewriting canonical records silently.
- Record tested restore duration, verified cutoff and remaining gaps; recovery
  time and acceptable loss are owner decisions, not guarantees. After the initial
  complete export, propose daily incremental verified copies: a one-day target
  RPO when backups succeed, with greater exposure after failures. Include periodic
  completeness reconciliation and restore checks. Cadence must match approved
  loss tolerance and costs.

[CloudFormation Retain](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html)
protects against stack-managed deletion, not account closure. Removing a stack
leaves retained buckets chargeable; deleting retained data requires explicit
authorization and is outside this design. Backup verification establishes what
still exists, not recovery of evidence already expired before retention changes.

## Proposed follow-up packages, in priority order

1. **Evidence export and recovery:** implement bounded private manifest/export
   tooling and integrity tests; separately approve live export, two-copy storage,
   consistent cutoff and restore test. Confirm live retention and quantify archive.
2. **Low-cost operator protection:** configure approved private budget/credit
   notices, two traffic alarms and manual disable procedure; validate destinations
   and price/allowance eligibility. No automatic destructive response.
3. **CloudFront Free compatibility:** prepare managed caching, security Function,
   global WAF/subscription templates and lifecycle tests. Resolve error-response
   approval and confirm account eligibility; reject paid-tier substitutions.
4. **Protected account/hosting transition:** execute only after independent review
   and gates A-C; preserve the disabled publisher until separately approved.
   If rejected, scope GitHub Pages/manual sanitized publication independently.

Outstanding decisions are those approval gates, error-response equivalence,
backup RPO/storage ownership and affordable recurring cost. Live eligibility,
tooling/region support, policy drift, actual usage and credit terms remain
implementation prerequisites. All external references above were consulted on
2026-10-11, except the explicitly inherited 2026-10-10 regional rate evidence;
reverify before migration. This document is a proposal and does not supersede
the existing operating contracts or authorize any live action.
