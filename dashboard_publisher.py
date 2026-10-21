"""Bounded, read-only telemetry retrieval and allowlisted public publication."""

from datetime import datetime, timedelta, timezone
import json
import math
import os
import re

from tornredeye.evidence_paths import LINEAGE, evidence_identity

UTC = timezone.utc
SNAPSHOT_KEY = "public/data.json"
MAX_BYTES = 192 * 1024
RECORD_BYTES = 1024 * 1024
PAGES = 2
EVENTS_PER_PAGE = 100
OBSERVATION = re.compile(r"persisted collection_id=([0-9a-f]{32}|[0-9a-f-]{36}) acquisition_status=success\b")
REPORT = re.compile(r"REPORT RequestId: ([0-9a-f-]{36}).*Billed Duration: ([0-9.]+) ms.*Memory Size: ([0-9]+) MB")


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def timestamp(value, now):
    try:
        if type(value) in (int, float):
            parsed = datetime.fromtimestamp(value, UTC)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if parsed.tzinfo is None or parsed > now or parsed.year < 2000:
            return None
        return stamp(parsed)
    except (ValueError, OverflowError, OSError):
        return None


def metric_specs(config):
    function = [{"Name": "FunctionName", "Value": config["COLLECTOR_FUNCTION"]}]
    publisher = [{"Name": "FunctionName", "Value": config["PUBLISHER_FUNCTION"]}]
    logs = [{"Name": "LogGroupName", "Value": config["COLLECTOR_LOG_GROUP"]}]
    topic = [{"Name": "TopicName", "Value": config["MONITORING_TOPIC_NAME"]}]
    bucket = config["EVIDENCE_BUCKET"]
    specs = {
        "persisted_events": (config["MONITORING_NAMESPACE"], "PersistedObservations", [], "Sum", "Count"),
        "halt_events": (config["MONITORING_NAMESPACE"], "PollingHalts", [], "Sum", "Count"),
    }
    for prefix, dimensions in (("collector", function), ("publisher", publisher)):
        for metric, stat, unit in (("Invocations", "Sum", "Count"), ("Errors", "Sum", "Count"),
                                   ("Duration", "Sum", "Milliseconds"), ("Throttles", "Sum", "Count")):
            specs[prefix + "_" + metric.lower()] = ("AWS/Lambda", metric, dimensions, stat, unit)
    specs["log_ingestion"] = ("AWS/Logs", "IncomingBytes", logs, "Sum", "Bytes")
    for name in ("NumberOfMessagesPublished", "NumberOfNotificationsDelivered", "NumberOfNotificationsFailed"):
        specs["sns_" + name] = ("AWS/SNS", name, topic, "Sum", "Count")
    for name, storage, unit in (("BucketSizeBytes", "StandardStorage", "Bytes"),
                                 ("NumberOfObjects", "AllStorageTypes", "Count")):
        dims = [{"Name": "BucketName", "Value": bucket}, {"Name": "StorageType", "Value": storage}]
        specs["s3_" + name] = ("AWS/S3", name, dims, "Average", unit)
    return specs


def read_metric(client, spec, start, end, period):
    namespace, name, dimensions, stat, unit = spec
    result = client.get_metric_statistics(Namespace=namespace, MetricName=name,
        Dimensions=dimensions, StartTime=start, EndTime=end, Period=period, Statistics=[stat])
    slots = [None] * int((end - start).total_seconds() // period)
    for point in result.get("Datapoints", []):
        date = point.get("Timestamp")
        if isinstance(date, datetime) and date.tzinfo is not None and start <= date < end:
            index = int((date - start).total_seconds() // period)
            if 0 <= index < len(slots):
                slots[index] = number(point.get(stat))
    return {"state": "available", "kind": "measured", "unit": unit,
            "provenance": "CloudWatch " + (namespace if namespace in ("AWS/Lambda", "AWS/S3", "AWS/Logs", "AWS/SNS") else "log-derived matching events"),
            "period_seconds": period, "start_utc": stamp(start), "end_utc": stamp(end),
            "latest_sample_utc": next((stamp(start + timedelta(seconds=i * period)) for i in range(len(slots) - 1, -1, -1) if slots[i] is not None), None),
            "samples": slots, "reported_slots": sum(v is not None for v in slots),
            "coverage": "unknown; returned samples do not establish complete collection coverage"}


def read_logs(client, group, pattern, start, end, budget):
    events, token = [], None
    for _ in range(PAGES):
        budget()
        args = dict(logGroupName=group, filterPattern=pattern,
                    startTime=int(start.timestamp() * 1000), endTime=int(end.timestamp() * 1000),
                    limit=EVENTS_PER_PAGE)
        if token:
            args["nextToken"] = token
        response = client.filter_log_events(**args)
        events.extend(response.get("events", [])[:EVENTS_PER_PAGE])
        next_token = response.get("nextToken")
        if not next_token:
            return events, "bounded query exhausted; delivery and retention may be incomplete"
        if next_token == token:
            break
        token = next_token
    return events, "partial; pagination limit reached"


def gap_candidates(series):
    """Missing telemetry is uncertainty, never a confirmed observation absence."""
    samples, result, run = series.get("samples", []), [], None
    start = datetime.fromisoformat(series["start_utc"].replace("Z", "+00:00"))
    for index, value in enumerate(samples + [1]):
        if value is None or value == 0:
            if run is None:
                run = index
        elif run is not None:
            if index - run >= 15:
                result.append({"start_utc": stamp(start + timedelta(minutes=run)),
                    "end_utc": stamp(start + timedelta(minutes=index)), "minutes": index - run,
                    "unknown_minutes": sum(v is None for v in samples[run:index]),
                    "kind": "derived", "coverage": "unconfirmed absence; no recorded positive persistence heartbeat"})
            run = None
    return result


def build_snapshot(clients, config, now, budget=lambda: None):
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    snapshot = {"schema_version": 1, "published_at_utc": stamp(now), "as_of_utc": stamp(now),
        "window": {"start_utc": stamp(start), "end_utc": stamp(end), "timezone": "UTC"},
        "metrics": {}, "recent_gaps": [], "freshness": {"state": "unavailable", "retrieved_at_utc": None,
            "source_timestamp_utc": None, "export_timestamp_utc": None, "kind": "measured",
            "provenance": "selected persisted all-stock acquisition; country freshness varies", "coverage": "latest candidate within two-hour logs only"},
        "billed_compute": {"state": "unavailable", "kind": "derived", "unit": "GB-seconds",
            "value": None, "start_utc": stamp(now - timedelta(hours=2)), "end_utc": stamp(now),
            "provenance": "deduplicated Lambda REPORT lines; billed duration and configured memory",
            "coverage": "bounded recent log subtotal, not a complete period or monthly bill"},
        "cost": {"kind": "estimated", "unit": "USD/month", "publisher_compute_scenario": [0.00790, 0.13965],
            "assumptions": "744 invocations; 128 MiB; assumed 5 to 90 billed seconds each; Stockholm x86 first-tier rates checked 2026-10-10; before allowances; extra invocations and initialization may add usage; not an actual bill or spending cap",
            "total": None, "coverage": "total unavailable: traffic, retries, stored logs, account-wide eligibility and actual billing unknown"}}
    specs = metric_specs(config)
    for name, spec in specs.items():
        try:
            budget()
            period = 86400 if name.startswith("s3_") else 3600
            metric_start = start.replace(hour=0) if period == 86400 else start
            metric_end = end.replace(hour=0) if period == 86400 else end
            snapshot["metrics"][name] = read_metric(clients["cloudwatch"], spec, metric_start, metric_end, period)
        except Exception:
            snapshot["metrics"][name] = {"state": "unavailable", "kind": "unavailable", "unit": spec[4],
                "samples": [], "coverage": "query failed or time budget exhausted"}
    recent_end = now.replace(second=0, microsecond=0)
    try:
        budget()
        recent = read_metric(clients["cloudwatch"], specs["persisted_events"], recent_end - timedelta(hours=3), recent_end, 60)
        snapshot["recent_heartbeat"] = recent
        snapshot["recent_gaps"] = gap_candidates(recent)
    except Exception:
        snapshot["recent_heartbeat"] = {"state": "unavailable", "samples": [], "coverage": "query unavailable"}
    log_start = now - timedelta(hours=2)
    try:
        events, coverage = read_logs(clients["logs"], config["COLLECTOR_LOG_GROUP"], '"persisted" "acquisition_status=success"', log_start, now, budget)
        candidates = []
        for event in sorted(events, key=lambda e: e.get("timestamp", 0), reverse=True):
            match = OBSERVATION.search(event.get("message", ""))
            if match:
                identity = match[1].replace("-", "")
                key_match = re.search(r"normalized_key=(\S+)", event.get("message", ""))
                if not key_match:
                    continue
                key = key_match[1]
                try:
                    _, key_id, kind = evidence_identity(key)
                except ValueError:
                    continue
                if kind == "attempt.json" and key_id.hex == identity and (identity, key) not in candidates:
                    candidates.append((identity, key))
        for identity, key in candidates[:3]:
            try:
                budget()
                response = clients["s3"].get_object(Bucket=config["EVIDENCE_BUCKET"], Key=key)
                stream = response["Body"]
                try:
                    body = stream.read(RECORD_BYTES + 1)
                finally:
                    stream.close()
                if len(body) > RECORD_BYTES:
                    continue
                record = json.loads(body)
                if (record.get("schema_version") != 1 or record.get("format") != "stock-evidence"
                        or record.get("lineage") != LINEAGE or record.get("acquisition", {}).get("status") != "success"
                        or record.get("validation_status") not in ("complete", "partial")
                        or str(record.get("collection_id", "")).replace("-", "") != identity):
                    continue
                retrieved = timestamp(record.get("retrieved_at"), now)
                if retrieved is None:
                    continue
                snapshot["freshness"].update(state="available", retrieved_at_utc=retrieved,
                    source_timestamp_utc=None,
                    export_timestamp_utc=timestamp(record.get("export_timestamp"), now), coverage=coverage + "; latest validated candidate, not guaranteed latest evidence")
                break
            except Exception:
                continue
    except Exception:
        pass
    try:
        events, coverage = read_logs(clients["logs"], config["COLLECTOR_LOG_GROUP"], '"REPORT"', log_start, now, budget)
        reports = {}
        for event in events:
            match = REPORT.search(event.get("message", ""))
            if match:
                duration, memory = number(float(match[2])), number(int(match[3]))
                if duration is not None and memory is not None:
                    reports[match[1]] = duration / 1000 * memory / 1024
        if reports:
            snapshot["billed_compute"].update(state="available", value=sum(reports.values()), coverage=coverage + "; deduplicated returned REPORT subtotal only")
    except Exception:
        pass
    snapshot["state"] = "partial" if any(m["state"] != "available" for m in snapshot["metrics"].values()) or snapshot["freshness"]["state"] != "available" or snapshot["billed_compute"]["state"] != "available" or snapshot["recent_heartbeat"]["state"] != "available" else "available"
    return snapshot


def publish(s3, bucket, snapshot):
    body = json.dumps(snapshot, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise ValueError("publication exceeds size bound")
    condition = {"IfNoneMatch": "*"}
    try:
        previous = s3.get_object(Bucket=bucket, Key=SNAPSHOT_KEY)
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") not in (
                "NoSuchKey", "404", "AccessDenied", "403"):
            raise
        # without ListBucket, a missing key can be denied; only attempt create-only
    else:
        stream = previous["Body"]
        try:
            old_body = stream.read(MAX_BYTES + 1)
        finally:
            stream.close()
        if len(old_body) > MAX_BYTES:
            raise ValueError("previous publication exceeds size bound")
        old = json.loads(old_body)
        if old.get("as_of_utc", old["published_at_utc"]) >= snapshot.get("as_of_utc", snapshot["published_at_utc"]):
            return False
        condition = {"IfMatch": previous["ETag"]}
    s3.put_object(Bucket=bucket, Key=SNAPSHOT_KEY, Body=body, ContentType="application/json",
                  CacheControl="public,max-age=300", **condition)
    return True


def publication_error_category(error):
    response = getattr(error, "response", {})
    details = response.get("Error", {}) if isinstance(response, dict) else {}
    code = details.get("Code") if isinstance(details, dict) else None
    if isinstance(code, str):
        return code if code in (
            "AccessDenied", "403", "NoSuchKey", "404", "NoSuchBucket",
            "PreconditionFailed", "ConditionalRequestConflict", "InvalidAccessKeyId",
            "SignatureDoesNotMatch", "ExpiredToken", "InvalidToken", "SlowDown",
            "InternalError", "ServiceUnavailable", "RequestTimeout") else "aws_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ValueError):
        return "invalid_snapshot"
    if isinstance(error, OSError):
        return "transport_error"
    return "unexpected_error"


def lambda_handler(event, context):
    import boto3
    from botocore.config import Config
    configuration = Config(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 2})
    clients = {name: boto3.client(name, config=configuration) for name in ("cloudwatch", "logs", "s3")}

    def budget():
        if context.get_remaining_time_in_millis() < 40000:
            raise TimeoutError("retrieval budget exhausted")

    snapshot = build_snapshot(clients, os.environ, datetime.now(UTC), budget)
    snapshot["published_at_utc"] = stamp(datetime.now(UTC))
    # no private telemetry, identifiers or exception text in publisher logs
    try:
        updated = publish(clients["s3"], os.environ["DASHBOARD_BUCKET"], snapshot)
    except Exception as error:
        print(f"dashboard publication failed ({publication_error_category(error)}); "
              "previous object retained or write outcome uncertain")
        raise RuntimeError("dashboard publication failed") from None
    print("dashboard publication completed" if updated else "dashboard publication superseded")
    return {"state": snapshot["state"], "updated": updated}
