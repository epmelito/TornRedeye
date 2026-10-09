"""Exercise S3 coordination and the actual Lambda flow without AWS."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from uuid import UUID, uuid4

import lambda_function as handler
from polling_guard import CONTROL_KEY, ControlError, PollingGuard
from s3_persistence import PersistenceError
from yata_collector import CollectionResult, collect, normalize

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
RUN_ID = UUID("12345678-1234-4234-8234-123456789abc")
RAW = b'{"timestamp":1,"stocks":{"jap":{"update":1,"stocks":[{"id":206,"name":"Xanax","quantity":0,"cost":800000}]}}}'


def utc(stamp):
    return stamp.isoformat().replace("+00:00", "Z")


def ready():
    return {"schema_version": 1, "revision": str(uuid4()), "updated_at": utc(NOW),
            "not_before": utc(NOW), "halt_reason": None, "attempt": None}


class S3Error(Exception):
    def __init__(self, status):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}}
        super().__init__(f"S3 HTTP {status}")


class MemoryS3:
    """Enforce conditional writes; inject failure before or after acceptance."""
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.get_error = None
        self.before_put = None
        self.after_put = None
        self.seed(ready())

    def seed(self, state):
        body = json.dumps(state).encode() if isinstance(state, dict) else state
        self.objects[CONTROL_KEY] = {"Body": body, "ETag": '"' + sha256(body).hexdigest() + '"'}

    def state(self):
        return json.loads(self.objects[CONTROL_KEY]["Body"])

    def get_object(self, *, Bucket, Key):
        if self.get_error:
            raise self.get_error
        if Key not in self.objects:
            raise S3Error(404)
        return {**self.objects[Key], "Body": BytesIO(self.objects[Key]["Body"])}

    def put_object(self, **request):
        self.puts.append(request)
        if self.before_put:
            self.before_put(request)
        key = request["Key"]
        old = self.objects.get(key)
        if "IfMatch" in request and (old is None or old["ETag"] != request["IfMatch"]):
            raise S3Error(412)
        if request.get("IfNoneMatch") == "*" and old is not None:
            raise S3Error(412)
        body = request["Body"]
        self.objects[key] = {"Body": body, "ETag": '"' + sha256(body).hexdigest() + '"',
                             "Metadata": request.get("Metadata", {})}
        if self.after_put:
            self.after_put(request)
        return {"ETag": self.objects[key]["ETag"]}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.s3 = MemoryS3()

    def guard(self):
        return PollingGuard(s3=self.s3, bucket="evidence", clock=lambda: self.now)

    def test_normal_cycle_requires_sixty_seconds_after_evidence_completion(self):
        guard = self.guard()
        self.assertIsNone(guard.acquire(RUN_ID))
        self.assertEqual(self.s3.state()["attempt"]["collection_id"], str(RUN_ID))
        self.now += timedelta(seconds=15)
        guard.record_outcome(normalize(RAW, self.now))
        self.now += timedelta(seconds=5)
        guard.finish()
        self.assertIsNone(self.s3.state()["attempt"])
        self.now += timedelta(seconds=59)
        self.assertTrue(self.guard().acquire(uuid4()).startswith("not_before="))
        self.now += timedelta(seconds=1)
        self.assertIsNone(self.guard().acquire(uuid4()))
        self.assertTrue(all(p["IfMatch"] for p in self.s3.puts))
        self.assertEqual(len({json.loads(p["Body"])["revision"] for p in self.s3.puts}), 4)

    def test_overlap_skips_and_expired_attempt_halts_without_takeover(self):
        first = self.guard()
        first.acquire(RUN_ID)
        self.assertEqual(self.guard().acquire(uuid4()), "in_flight")
        self.now += timedelta(seconds=180)
        self.assertTrue(self.guard().acquire(uuid4()).startswith("halted: stale"))
        self.assertEqual(self.s3.state()["attempt"]["collection_id"], str(RUN_ID))
        self.now += timedelta(days=1)
        self.assertTrue(self.guard().acquire(uuid4()).startswith("halted:"))

    def test_competing_acquisitions_use_compare_and_swap(self):
        competitor = self.guard()
        def race(request):
            self.s3.before_put = None
            self.assertIsNone(competitor.acquire(uuid4()))
        self.s3.before_put = race
        self.assertEqual(self.guard().acquire(RUN_ID), "control_conflict")
        self.assertNotEqual(self.s3.state()["attempt"]["collection_id"], str(RUN_ID))

    def test_409_conflict_skips_without_retry(self):
        self.s3.before_put = lambda request: (_ for _ in ()).throw(S3Error(409))
        self.assertEqual(self.guard().acquire(RUN_ID), "control_conflict")
        self.assertEqual(len(self.s3.puts), 1)

    def test_429_cooldown_survives_release_and_new_instances(self):
        guard = self.guard()
        guard.acquire(RUN_ID)
        deadline = NOW + timedelta(minutes=10)
        result = CollectionResult("collection_failed", NOW, b"limited", http_status=429,
                                  retry_after_at=utc(deadline))
        guard.record_outcome(result)
        guard.finish()
        self.now = deadline - timedelta(microseconds=1)
        self.assertTrue(self.guard().acquire(uuid4()).startswith("not_before="))
        self.now = deadline
        self.assertIsNone(self.guard().acquire(uuid4()))

    def test_restrictions_halt_durably_until_operator_intervention(self):
        cases = [(401, None, None), (403, utc(NOW), None),
                 (429, None, None), (429, None, "invalid Retry-After")]
        for status, deadline, error in cases:
            with self.subTest(status=status, error=error):
                self.s3.seed(ready())
                guard = self.guard()
                guard.acquire(RUN_ID)
                guard.record_outcome(CollectionResult(
                    "collection_failed", NOW, b"restricted", http_status=status,
                    retry_after_at=deadline, retry_after_error=error))
                guard.finish()
                self.now = NOW + timedelta(days=7)
                self.assertTrue(self.guard().acquire(uuid4()).startswith(f"halted: HTTP {status}"))
                self.assertIsNone(self.s3.state()["attempt"])
                self.now = NOW

    def test_transient_failures_allow_next_eligible_opportunity(self):
        for status in (None, 500, 503):
            with self.subTest(status=status):
                self.s3.seed(ready())
                guard = self.guard()
                guard.acquire(RUN_ID)
                guard.record_outcome(CollectionResult("collection_failed", NOW, None, http_status=status))
                guard.finish()
                self.now += timedelta(seconds=60)
                self.assertIsNone(self.guard().acquire(uuid4()))
                self.now = NOW

    def test_503_valid_retry_after_is_also_respected(self):
        guard = self.guard()
        guard.acquire(RUN_ID)
        guard.record_outcome(CollectionResult("collection_failed", NOW, b"unavailable",
                             http_status=503, retry_after_at=utc(NOW + timedelta(minutes=5))))
        guard.finish()
        self.now += timedelta(minutes=1)
        self.assertTrue(self.guard().acquire(uuid4()).startswith("not_before="))

    def test_missing_unreadable_or_malformed_state_fails_closed(self):
        broken = [b"bad JSON", b'{"schema_version":1,"schema_version":1}', b"null",
                  b"x" * 16385, {**ready(), "schema_version": True},
                  {**ready(), "revision": "bad"}, {**ready(), "extra": 1},
                  {**ready(), "not_before": "2026-10-09T12:00:00"},
                  {**ready(), "not_before": "2026-10-09T13:00:00+01:00"},
                  {**ready(), "updated_at": utc(NOW + timedelta(seconds=1))},
                  {**ready(), "halt_reason": ""},
                  {**ready(), "attempt": {"collection_id": str(RUN_ID),
                    "acquired_at": utc(NOW), "expires_at": utc(NOW + timedelta(seconds=1))}}]
        for state in broken:
            with self.subTest(state=state):
                self.s3.seed(state)
                with self.assertRaises(ControlError):
                    self.guard().acquire(RUN_ID)
        for status in (403, 404, 500):
            self.s3.get_error = S3Error(status)
            with self.assertRaises(ControlError):
                self.guard().acquire(RUN_ID)
        self.assertEqual(self.s3.puts, [])

    def test_missing_etag_cannot_acquire(self):
        del self.s3.objects[CONTROL_KEY]["ETag"]
        with self.assertRaises(ControlError):
            self.guard().acquire(RUN_ID)
        self.assertEqual(self.s3.puts, [])

    def test_ambiguous_acquisition_acknowledgement_leaves_attempt_blocked(self):
        self.s3.after_put = lambda request: (_ for _ in ()).throw(TimeoutError("lost acknowledgement"))
        with self.assertRaises(ControlError):
            self.guard().acquire(RUN_ID)
        self.s3.after_put = None
        self.assertEqual(self.guard().acquire(uuid4()), "in_flight")

    def test_policy_conflict_cannot_overwrite_operator_halt(self):
        guard = self.guard()
        guard.acquire(RUN_ID)
        state = self.s3.state()
        self.s3.seed({**state, "revision": str(uuid4()), "halt_reason": "operator halt"})
        with self.assertRaises(ControlError):
            guard.record_outcome(normalize(RAW, NOW))
        self.assertEqual(self.s3.state()["halt_reason"], "operator halt")

    def test_stale_state_write_failure_does_not_clear_attempt(self):
        self.guard().acquire(RUN_ID)
        self.now += timedelta(seconds=180)
        self.s3.before_put = lambda request: (_ for _ in ()).throw(OSError("S3 unavailable"))
        with self.assertRaises(ControlError):
            self.guard().acquire(uuid4())
        self.assertIsNotNone(self.s3.state()["attempt"])



class BoundedWaitTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.s3 = MemoryS3()
        self.sleeps = []
        self.remaining = 120000

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)
        self.remaining -= seconds * 1000

    def guard(self, sleeper=None):
        return PollingGuard(s3=self.s3, bucket="evidence", clock=lambda: self.now,
                            sleeper=sleeper or self.sleep)

    def acquire(self, guard=None):
        return (guard or self.guard()).acquire(uuid4(), remaining_millis=lambda: self.remaining)

    def spacing(self, seconds):
        self.s3.seed({**ready(), "spacing_only": True,
                      "not_before": utc(self.now + timedelta(seconds=seconds))})

    def test_consecutive_schedule_opportunities_with_nonzero_duration_and_jitter(self):
        for jitter in ([0] * 12, [0, 4, -3, 6, -5, 2, 0, 5, -2, 1, -4, 3]):
            with self.subTest(jitter=jitter):
                self.setUp()
                requests = []
                skipped = 0
                for slot, offset in enumerate(jitter):
                    self.now = NOW + timedelta(seconds=slot * 60 + offset)
                    self.remaining = 120000
                    guard = self.guard()
                    reason = self.acquire(guard)
                    if reason is not None:
                        skipped += 1
                        continue
                    requests.append(self.now)
                    self.now += timedelta(seconds=3)
                    guard.record_outcome(normalize(RAW, self.now))
                    self.now += timedelta(seconds=1)
                    guard.finish()
                self.assertGreaterEqual(len(requests), 9)
                self.assertLessEqual(skipped, 3)
                self.assertTrue(all((b - a).total_seconds() >= 60
                                    for a, b in zip(requests, requests[1:])))
                self.assertTrue(all(0 < delay <= 15 for delay in self.sleeps))
                if jitter == [0] * 12:
                    self.assertEqual(len(requests), 10)
                    self.assertEqual([int((t - NOW).total_seconds()) for t in requests],
                                     [0, 64, 128, 192, 300, 364, 428, 492, 600, 664])

    def test_wait_boundary_and_execution_budget(self):
        for delay, budget, expected in ((15, 75000, True), (15.001, 120000, False),
                                        (10, 69999, False), (10, 70000, True)):
            with self.subTest(delay=delay, budget=budget):
                self.setUp()
                self.spacing(delay)
                self.remaining = budget
                reason = self.acquire()
                self.assertEqual(reason is None, expected)
                self.assertEqual(len(self.sleeps), int(expected))
        self.setUp()
        self.spacing(0)
        self.remaining = 59999
        self.assertEqual(self.acquire(), "insufficient_execution_time")
        self.assertEqual(self.s3.puts, [])

    def test_never_waits_on_halts_active_stale_or_unknown_state(self):
        cases = [
            {**ready(), "spacing_only": True, "halt_reason": "operator hold"},
            {**ready(), "spacing_only": True, "attempt": {
                "collection_id": str(RUN_ID), "acquired_at": utc(NOW),
                "expires_at": utc(NOW + timedelta(seconds=180))}},
            {**ready(), "spacing_only": True, "updated_at": utc(NOW - timedelta(seconds=180)),
             "attempt": {"collection_id": str(RUN_ID), "acquired_at": utc(NOW - timedelta(seconds=180)),
                         "expires_at": utc(NOW)}},
        ]
        for state in cases:
            self.s3.seed(state)
            self.assertIsNotNone(self.acquire())
        self.s3.seed(b"invalid")
        with self.assertRaises(ControlError):
            self.acquire()
        self.assertEqual(self.sleeps, [])

    def test_old_state_and_provider_cooldowns_never_wait_even_one_second_early(self):
        self.s3.seed({**ready(), "not_before": utc(NOW + timedelta(seconds=1))})
        self.assertIsNotNone(self.acquire())
        for status in (429, 503):
            guard = self.guard()
            self.s3.seed(ready())
            guard.acquire(RUN_ID)
            guard.record_outcome(CollectionResult(
                "collection_failed", self.now, b"restricted", http_status=status,
                retry_after_at=utc(self.now + timedelta(seconds=120))))
            guard.finish()
            self.now += timedelta(seconds=119)
            self.assertIsNotNone(self.acquire())
            self.now += timedelta(seconds=1)
            self.assertIsNone(self.acquire())
            self.now = NOW
        self.assertEqual(self.sleeps, [])

    def test_competitor_during_wait_wins_and_waiter_does_not_overlap(self):
        self.spacing(5)
        competitor = self.guard()
        def sleep_with_competitor(seconds):
            self.sleep(seconds)
            self.assertIsNone(competitor.acquire(RUN_ID))
        self.assertEqual(self.acquire(self.guard(sleep_with_competitor)), "in_flight")
        self.assertEqual(self.sleeps, [5])
        self.assertEqual(self.s3.state()["attempt"]["collection_id"], str(RUN_ID))

    def test_competing_conditional_write_after_wait_skips_without_retry(self):
        self.spacing(5)
        def race(request):
            self.s3.before_put = None
            self.assertIsNone(self.guard().acquire(RUN_ID))
        self.s3.before_put = race
        self.assertEqual(self.acquire(), "control_conflict")
        self.assertEqual(self.sleeps, [5])

    def test_changed_state_after_wait_is_read_and_never_waits_again(self):
        cases = [
            {**ready(), "halt_reason": "operator halt"},
            {**ready(), "spacing_only": False, "not_before": utc(NOW + timedelta(minutes=10))},
            {**ready(), "spacing_only": True, "not_before": utc(NOW + timedelta(seconds=10))},
            b"bad state",
        ]
        for state in cases:
            with self.subTest(state=state):
                self.setUp()
                self.spacing(5)
                def change_after_sleep(seconds):
                    self.sleep(seconds)
                    self.s3.seed(state)
                if isinstance(state, bytes):
                    with self.assertRaises(ControlError):
                        self.acquire(self.guard(change_after_sleep))
                else:
                    self.assertIsNotNone(self.acquire(self.guard(change_after_sleep)))
                self.assertEqual(self.sleeps, [5])
                self.assertEqual(self.s3.puts, [])

    def test_early_wake_and_insufficient_post_wait_budget_skip(self):
        self.spacing(5)
        self.assertIsNotNone(self.acquire(self.guard(lambda seconds: self.sleeps.append(seconds))))
        self.assertEqual(self.sleeps, [5])
        self.setUp()
        self.spacing(5)
        def late_wake(seconds):
            self.sleep(seconds)
            self.remaining = 59999
        self.assertEqual(self.acquire(self.guard(late_wake)), "insufficient_execution_time")
        self.assertEqual(self.s3.puts, [])

    def test_sleep_interruption_or_s3_read_failure_never_acquires(self):
        self.spacing(5)
        def interrupt(seconds):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.acquire(self.guard(interrupt))
        self.assertIsNone(self.s3.state()["attempt"])
        def fail_read(seconds):
            self.sleep(seconds)
            self.s3.get_error = OSError("S3 unavailable")
        with self.assertRaises(ControlError):
            self.acquire(self.guard(fail_read))
        self.assertEqual(self.s3.puts, [])

    def test_invalid_spacing_flag_fails_closed(self):
        self.s3.seed({**ready(), "spacing_only": "true"})
        with self.assertRaises(ControlError):
            self.acquire()
        self.assertEqual(self.sleeps, [])


class GuardedHandlerTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.s3 = MemoryS3()
        self.sleeps = []
        self.context = SimpleNamespace(aws_request_id="test-invocation",
                                       get_remaining_time_in_millis=lambda: 120000)
        for patcher in (
            patch.dict("os.environ", {"DESTINATION_BUCKET": "evidence"}, clear=True),
            patch("lambda_function._s3_client", return_value=self.s3),
            patch("lambda_function.PollingGuard", side_effect=lambda **kw: PollingGuard(
                **kw, clock=lambda: self.now, sleeper=self.sleep)),
            patch("lambda_function.uuid4", side_effect=lambda: uuid4()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.collect_patch = patch("lambda_function.collect", return_value=normalize(RAW, NOW))
        self.collect = self.collect_patch.start()
        self.addCleanup(self.collect_patch.stop)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)

    def invoke(self):
        with self.assertLogs("lambda_function", level="INFO"):
            return handler.lambda_handler({}, self.context)

    def evidence(self):
        return {key: value for key, value in self.s3.objects.items() if key != CONTROL_KEY}

    def test_same_event_skips_until_eligible_then_preserves_distinct_cached_retrievals(self):
        first = self.invoke()
        skipped = self.invoke()
        self.assertEqual(skipped["status"], "skipped")
        self.assertTrue(skipped["reason"].startswith("not_before="))
        self.assertEqual(self.collect.call_count, 1)
        self.now += timedelta(seconds=60)
        second = self.invoke()
        self.assertNotEqual(first["collection_id"], second["collection_id"])
        self.assertEqual(len(self.evidence()), 4)
        self.assertEqual(self.collect.call_count, 2)
        for summary in (first, second):
            document = json.loads(self.s3.objects[summary["normalized_key"]]["Body"])
            self.assertEqual(document["collection_id"], summary["collection_id"])
            self.assertEqual(document["observation"]["quantity"], 0)
            self.assertEqual(document["source_timestamp"], 1)

    def test_handler_waits_and_preserves_request_spacing_and_distinct_evidence(self):
        requests = []
        def retrieve(**kwargs):
            requests.append(self.now)
            self.now += timedelta(seconds=3)
            return normalize(RAW, self.now)
        self.collect.side_effect = retrieve
        first = self.invoke()
        self.now = NOW + timedelta(seconds=60)
        second = self.invoke()
        self.assertEqual(self.sleeps, [3])
        self.assertEqual((requests[1] - requests[0]).total_seconds(), 63)
        self.assertNotEqual(first["collection_id"], second["collection_id"])
        self.assertEqual(len(self.evidence()), 4)

    def test_skip_paths_never_collect_or_write_evidence(self):
        states = [{**ready(), "halt_reason": "operator hold"},
                  {**ready(), "not_before": utc(NOW + timedelta(seconds=60))},
                  {**ready(), "attempt": {"collection_id": str(RUN_ID),
                    "acquired_at": utc(NOW), "expires_at": utc(NOW + timedelta(seconds=180))}}]
        for state in states:
            self.s3.seed(state)
            self.assertEqual(self.invoke()["status"], "skipped")
        self.collect.assert_not_called()
        self.assertEqual(self.evidence(), {})

    def test_bad_control_state_causes_invocation_error_before_yata(self):
        self.s3.seed(b"bad")
        with self.assertRaises(ControlError):
            self.invoke()
        self.collect.assert_not_called()

    def test_real_http_failures_preserve_headers_body_policy_and_failure(self):
        self.collect.side_effect = lambda **kw: collect(**kw)
        cases = [(429, {"Retry-After": "120"}, "not_before="),
                 (429, {}, "halted:"), (429, {"Retry-After": "invalid"}, "halted:"),
                 (401, {}, "halted:"), (403, {}, "halted:"),
                 (503, {}, "not_before=")]
        for status, headers, reason in cases:
            with self.subTest(status=status, headers=headers):
                self.s3 = MemoryS3()
                with patch("lambda_function._s3_client", return_value=self.s3):
                    error = HTTPError("https://yata.yt/api/v1/travel/export/", status,
                                      "failure", headers, BytesIO(b"server error"))
                    with patch("yata_collector.urlopen", side_effect=error) as request:
                        with patch("yata_collector.datetime") as clock:
                            clock.now.return_value = NOW
                            with self.assertRaises(handler.CollectionError):
                                self.invoke()
                    document = json.loads(next(v["Body"] for k, v in self.evidence().items()
                                               if k.startswith("normalized/")))
                    self.assertEqual(document["http_status"], status)
                    self.assertEqual(document["response_headers"], list(map(list, headers.items())))
                    self.assertEqual(next(v["Body"] for k, v in self.evidence().items()
                                          if k.startswith("raw/")), b"server error")
                    skipped = self.invoke()
                    self.assertEqual(skipped["status"], "skipped")
                    self.assertTrue(skipped["reason"].startswith(reason))
                    request.assert_called_once()
                    self.assertIsNone(self.s3.state()["attempt"])

    def test_network_failure_is_persisted_and_next_eligible_run_can_succeed(self):
        self.collect.side_effect = lambda **kw: collect(**kw)
        with patch("yata_collector.urlopen", side_effect=URLError("offline")):
            with patch("yata_collector.datetime") as clock:
                clock.now.return_value = NOW
                with self.assertRaises(handler.CollectionError):
                    self.invoke()
        self.assertEqual(len(self.evidence()), 1)
        self.assertEqual(self.invoke()["status"], "skipped")
        self.now += timedelta(seconds=60)
        self.collect.side_effect = None
        self.collect.return_value = normalize(RAW, self.now)
        self.assertEqual(self.invoke()["status"], "observed")

    def test_interrupted_execution_leaves_lease_and_later_halts(self):
        self.collect.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.invoke()
        self.assertEqual(self.invoke()["reason"], "in_flight")
        self.now += timedelta(seconds=180)
        self.assertTrue(self.invoke()["reason"].startswith("halted: stale"))
        self.assertEqual(self.collect.call_count, 1)
        self.assertEqual(self.evidence(), {})

    def test_partial_evidence_failure_preserves_raw_and_does_not_release(self):
        self.s3.before_put = lambda p: (_ for _ in ()).throw(OSError("storage failed")) if p["Key"].startswith("normalized/") else None
        with self.assertRaises(PersistenceError):
            self.invoke()
        self.assertEqual(len(self.evidence()), 1)
        self.assertEqual(self.invoke()["reason"], "in_flight")
        self.now += timedelta(seconds=180)
        self.assertTrue(self.invoke()["reason"].startswith("halted:"))

    def test_policy_failure_still_preserves_evidence_and_does_not_release(self):
        count = 0
        def fail_policy(request):
            nonlocal count
            if request["Key"] == CONTROL_KEY:
                count += 1
                if count == 2:
                    raise OSError("policy unavailable")
        self.s3.before_put = fail_policy
        with self.assertRaises(ControlError):
            self.invoke()
        self.assertEqual(len(self.evidence()), 2)
        self.assertEqual(self.invoke()["reason"], "in_flight")

    def test_halt_survives_evidence_storage_failure(self):
        self.collect.return_value = CollectionResult("collection_failed", NOW, b"restricted", http_status=403)
        self.s3.before_put = lambda p: (_ for _ in ()).throw(OSError("storage failed")) if p["Key"].startswith("raw/") else None
        with self.assertRaises(PersistenceError):
            self.invoke()
        self.assertTrue(self.invoke()["reason"].startswith("halted: HTTP 403"))
        self.assertEqual(self.collect.call_count, 1)

    def test_release_failure_is_visible_even_when_evidence_is_complete(self):
        count = 0
        def fail_release(request):
            nonlocal count
            if request["Key"] == CONTROL_KEY:
                count += 1
                if count == 3:
                    raise OSError("release failed")
        self.s3.before_put = fail_release
        with self.assertRaises(ControlError):
            self.invoke()
        self.assertEqual(len(self.evidence()), 2)
        self.assertEqual(self.invoke()["reason"], "in_flight")


if __name__ == "__main__":
    unittest.main()
