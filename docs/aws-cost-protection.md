# Post-trial AWS cost protection and evidence continuity

Architecture assessment for [Issue #17](https://github.com/epmelito/TornRedeye/issues/17).
Research verified **2026-10-11**; proposals below are not approved deployments.
No resources, schedules, account plans, subscriptions or evidence were changed
by that assessment. Later operator-verified hosting state is recorded below.

This is a date-scoped assessment, not current operational status. Subsequent
backup activation and recovery verification are recorded in
[deployment status](deployment.md#verified-operational-status). The account/hosting
proposals and October 11 financial baseline below remain historical; later backup
success does not establish their implementation or the publisher's enabled state.

## Recommendation

The [operator-verified hosting record](deployment.md#verified-hosting-state-and-ownership)
documents ACTIVE console-owned CloudFront FREE coverage, the existing WAF and
disabled delivery. Preserve that coverage and disabled delivery while resolving the
error-response security and source-redistribution gates; do not create or import
another subscription.

The October 11 FREE-account/credit baseline below remains historical evidence;
it does not establish current account plan, remaining credits or publisher state.
The earlier proposed account/hosting transition is no longer the current
subscription-creation procedure. Current maintenance and rollback boundaries
are recorded in deployment; no new account upgrade or paid-tier fallback is proposed.

## Historical evidence and risks (2026-10-11)

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

**Material risks at that baseline:** public pay-as-you-go requests could consume
credits and incur uncapped usage charges after upgrade; cache misses also reach S3. Account closure can
make all evidence inaccessible. Stack retention does not prevent privileged
deletion, lifecycle expiration, corruption or account-level deletion. Exact
archive size, historical losses, current request volume, credit expiry details
and account-wide remaining allowances are unavailable here.

## Historical account-transition assessment (2026-10-11)

This section preserves the October 11 assessment, not a current transition
procedure. The AWS account billing plan is separate from a CloudFront FREE
subscription; the later hosting observations do not establish current account
plan or credit balances.

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

The October 11 proposal was to prepare compatibility changes, export/recovery
tooling, independent backups and operator procedures while still on the Free
account plan, with budget permissions confirmed. Account upgrade and subscription
creation were then subject to later approval and eligibility verification;
[PricingPlanManager](https://docs.aws.amazon.com/PricingPlanManager/latest/UserGuide/plans.html)
excluded Free Tier accounts in that assessment. The later console-created ACTIVE
subscription supersedes that prospective creation step; do not create another.

## Hosting options and cost exposure

The following October 11 alternatives preserve the historical comparison; the
FREE row's conditional-target wording is not a description of today's recorded
hosting state. USD estimates exclude taxes, credits and uncertain account allowances. They are
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
Neither was proportionate merely to preserve the two historical custom policies.
Before future operations, freshly verify the existing subscription's eligible
resources and exclusions; this comparison authorizes no new subscription.

Issue #75's local candidate was prepared on **2026-10-29**, corrected for the
byte-match rejection on **2026-10-30**, and reconciled to the operator-verified
ACTIVE FREE state on **2026-10-31**. The dashboard condition now selects
PriceClass_All in FREE mode; PriceClass_100 and custom policy resources remain
historical/noncandidate template settings, not authorized rollback choices while
FREE remains active. The [dashboard compatibility contract](dashboard.md#cloudfront-free-candidate-compatibility)
owns cache/security behavior, and the [deployment procedure](deployment.md#cloudfront-free-activation-boundary)
owns verified state, console ownership, remaining gates and rollback boundaries.

Private S3/OAC, the operations root, managed policies, viewer-response Function
and existing WAF association are preserved. The byte-match rule is verified
removed live. This loses COUNT-only diagnostics, not enforced blocking. The
remaining local native IP rate rule defaults to COUNT and does not reduce misses.

**ACTIVE FREE is not security-equivalence approval:** origin errors still bypass
the Function, and error CSP/framing/referrer differences remain unresolved.
Keep public delivery disabled until the explicit security and source-redistribution
gates are satisfied. Reattaching incompatible custom policies or detaching WAF
while FREE remains active is not an authorized rollback. No meta CSP, status-200
error remapping, replacement service or separately billed Lambda@Edge is introduced.

The subscription was created through the console and is outside CloudFormation.
Its unused local creation template and exclusive test have been removed; no runtime
or recovery dependency required them. Earlier offline schema/lint findings are
preserved as [dated validation evidence](dashboard.md#publisher-and-infrastructure-validation),
not as current instructions to create/import a subscription or expand creation
permissions. The existing dashboard stack still owns distribution associations;
the ACL template remains separate. The existing ACL was confirmed to belong to
the deployed `tornredeye-cloudfront-waf` stack in `us-east-1`; its successful
update and current single COUNT rate rule were independently verified. Require
fresh verification before future operations rather than treating this evidence
as a permanent guarantee of current state.

Expected **included** delivery/protection price is $0/month under the reported
ACTIVE FREE coverage for the existing distribution and associated ACL. This does
not cap the account's $5 budget. S3 origin GET/PUT, publication/collector Lambda, evidence and
artifact storage, logs and unrelated services remain variable. One continuously
visible stock tab makes about 86,400 polls in 30 days before failures/latency;
roughly 11.6 such tabs consume 1M requests before other routes/assets. This is an
arithmetic scenario, not a traffic forecast. More edge locations/variants and
malicious unique paths can increase billable S3 misses even under the plan.

If coverage is lost, cancelled or a resource becomes orphaned, the one-rule WAF
has a reference standalone baseline of **$6/month** ($5 ACL + $1 rule), prorated
hourly, plus $0.60/million inspected requests. That is residual exposure, not a
claim of current standalone WAF billing under ACTIVE FREE. COUNT does not remove
fees outside coverage. Disabling delivery or detaching WAF does not delete its
billable resources; do not detach it from the active plan. A separately approved
cancellation transition would need verified coverage state, disabled delivery and
explicit ACL cleanup ownership. The earlier 30-minute bridge was planning, not a
current activation step or a hard cost cap. Budget notices are delayed alerts,
not a spending cap. [WAF pricing reference](https://aws.amazon.com/waf/pricing/).

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

The October 11 assessment proposed an account transition, WAF setup and later
subscription creation, with future planning milestones of February 28 and
March 15, 2027 against the then-recorded credit expiry. Those were proposals,
not performed operations or current deadlines. The dated account/credit evidence
above is preserved; the October 31 ACTIVE console-created subscription supersedes
that prospective creation sequence. Do not repeat it or apply historical account
assumptions to the current account.

Use the [current failure and rollback boundary](deployment.md#failure-and-rollback-boundary):
keep delivery disabled, retain ACTIVE FREE, its WAF association, managed policies,
Function and PriceClass_All. Reverting to incompatible custom policies or detaching
WAF while FREE remains active is not authorized. Console subscription lifecycle
is independent of dashboard stack rollback. Do not create or import another
subscription. Cancellation, account changes or pay-as-you-go public delivery require separate explicit owner
authorization; none is a default recovery action. Preserve backup, evidence,
collector independence and current publication permissions throughout.

## Evidence continuity and recovery requirements

The [current backup contract](evidence-backup.md) covers the operational lineage,
immutable prospective records and its independent empty-lineage anchor. Preserve
the original archive, pins and recovery prerequisites separately.

The [evidence backup workflow](evidence-backup.md) documents the active tooling and
verification contract. Dated backup/recovery evidence is recorded in deployment
status; neither this proposal nor those past results authorize further exports,
storage changes, collector pauses or restoration.

The approved Issue #23 architecture has exactly **two persistent evidence
copies: canonical AWS S3 primary and one independent Azure Blob Storage backup**.
Temporary local exports are disposable staging, not another persistent copy.
Preserve the verified baseline archive and its recovery prerequisites; no
additional storage provider or encryption layer is required. Do not publish
backups or manifests in GitHub/dashboard output. Live export, cloud configuration
and recurring backup activation remain separately authorized operations.

- Export every object in the declared immutable operational evidence/forecast prefixes, including malformed, missing,
  failure, empty-body, full foreign-market payload and raw-only partial writes.
  Do not filter to observed status, latest records or a selected country/item's
  stock rows.
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
  or deletion. ETags alone are not universal content hashes. Verify the Azure
  backup and its recovery against the manifest; unresolved transfers prevent a
  completeness claim.
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
  complete export, maintain daily incremental verified copies: a one-day target
  RPO when backups succeed, with greater exposure after failures. Include periodic
  completeness reconciliation and restore checks. Cadence must match approved
  loss tolerance and costs.

[CloudFormation Retain](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-deletionpolicy.html)
protects against stack-managed deletion, not account closure. Removing a stack
leaves retained buckets chargeable; deleting retained data requires explicit
authorization and is outside this design. Backup verification establishes what
still exists, not recovery of evidence already expired before retention changes.

## Proposed follow-up packages, in priority order

1. **Evidence export and recovery:** tooling, daily Azure backup and independent
   recovery have since been verified as recorded in deployment status. Preserve
   those capabilities; reassess live retention, coverage and archive size before
   a future account/hosting transition.
2. **Low-cost operator protection:** configure approved private budget/credit
   notices, two traffic alarms and manual disable procedure; validate destinations
   and price/allowance eligibility. No automatic destructive response.
3. **CloudFront Free compatibility:** the operator reports ACTIVE console-owned
   FREE with the correct managed policies/Function/WAF and disabled delivery.
   Review the local PriceClass reconciliation and resolve error-response approval;
   do not recreate/import the subscription or substitute a paid tier.
4. **Public delivery decision:** retain disabled delivery until independent review,
   source-redistribution clearance, security acceptance and explicit enablement
   authorization. Keep collection and publication permissions unchanged.

Outstanding decisions include public-delivery security/redistribution gates,
backup RPO/storage ownership and affordable recurring cost. Current account/credit
terms, complete configuration drift and actual usage were not supplied; ACTIVE
FREE does not establish those facts. All external references above were consulted on
2026-10-11, except the explicitly inherited 2026-10-10 regional rate evidence;
reverify before migration. This document is a proposal and does not supersede
the existing operating contracts or authorize any live action.
