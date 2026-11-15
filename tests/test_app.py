import unittest
import smtplib
import json
from dataclasses import replace
from unittest.mock import call, patch

from test_core import RuntimeTest
from visa_tool.app import Scheduler
from visa_tool.core import AccessBlocked, SafetyError, Slot, WorkerLost


class FakeBrowser:
    def __init__(self):
        self.calls = []
        self.state = "schedule"
        self.days = ["2026-12-01", "2026-12-16", "2026-12-23"]
        self.result = "success"
        self.submission_error = None
        self.blocks = 0
        self.closed = False

    def call(self, method, *args, **kwargs):
        self.calls.append((method, args))
        if method == "inspect":
            return {"state": self.state}
        if method == "load_dates":
            if self.blocks:
                self.blocks -= 1
                raise AccessBlocked("rate limit", retry_after=3)
            return self.days
        if method == "prepare_slot":
            return Slot(args[0], "08:30", "2", 7)
        if method == "gui_verify":
            return {"attempted": False}
        if method == "submit" and self.submission_error:
            raise self.submission_error
        if method == "outcome":
            return self.result
        return True

    def close(self):
        self.closed = True


class SchedulerTests(RuntimeTest):
    def scheduler(self, browser, **changes):
        self.config = replace(self.config, **changes)
        self.runtime.config = self.config
        return Scheduler(self.config, self.runtime, lambda *_: browser)

    def test_monitor_only_never_selects_or_submits(self):
        browser = FakeBrowser()
        self.scheduler(browser).run()
        methods = [method for method, _ in browser.calls]
        self.assertNotIn("prepare_slot", methods)
        self.assertNotIn("submit", methods)
        self.assertFalse(self.store.attempt_path.exists())
        self.assertTrue(any("Preferred" in subject for subject, _ in self.messages))
        self.assertTrue(browser.closed)

    def test_email_preflight_failure_prevents_browser_start(self):
        created = []

        def broken(*_):
            raise smtplib.SMTPException("offline")
        self.mail.sender = broken
        scheduler = Scheduler(self.config, self.runtime, lambda *_: created.append(True))
        self.assertEqual(scheduler.run(), "error")
        self.assertEqual(created, [])
        self.assertTrue(self.store.data["stopped"])

    def test_fresh_random_delay_between_observations_includes_both_bounds(self):
        browser = FakeBrowser()
        scheduler = self.scheduler(browser, max_checks=3, poll_seconds_min=2, poll_seconds_max=5)
        with patch("visa_tool.app.random.randint", side_effect=[2, 5]) as sample:
            self.assertEqual(scheduler.run(), "stopped")
        self.assertEqual(sample.call_args_list, [call(2, 5), call(2, 5)])
        self.assertEqual(self.clock.elapsed, 7)
        self.assertEqual(sum(method == "load_dates" for method, _ in browser.calls), 3)
        records = [
            json.loads(line)
            for path in (self.config.root / "logs").glob("EventLog_*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        waits = [record for record in records if record["action"] == "POLL_WAIT"]
        self.assertEqual([record["delay_seconds"] for record in waits], [2, 5])
        self.assertTrue(all(record["minimum_seconds"] == 2 and record["maximum_seconds"] == 5 for record in waits))

    def test_equal_bounds_produce_fixed_delay_and_no_terminal_wait(self):
        scheduler = self.scheduler(FakeBrowser(), max_checks=3, poll_seconds_min=4, poll_seconds_max=4)
        self.assertEqual(scheduler.run(), "stopped")
        self.assertEqual(self.clock.elapsed, 8)

    def test_no_random_wait_when_no_next_observation(self):
        with patch("visa_tool.app.random.randint") as sample:
            self.assertEqual(self.scheduler(FakeBrowser(), max_checks=1).run(), "stopped")
        sample.assert_not_called()
        self.assertEqual(self.clock.elapsed, 0)

    def test_no_preferred_days_never_clicks(self):
        browser = FakeBrowser()
        browser.days = ["2026-12-16", "2027-01-01"]
        self.scheduler(browser, auto_booking=True).run()
        self.assertNotIn("prepare_slot", [method for method, _ in browser.calls])
        self.assertFalse(self.store.attempt_path.exists())

    def test_empty_availability_is_not_retry_error(self):
        browser = FakeBrowser()
        browser.days = []
        self.scheduler(browser).run()
        self.assertEqual(self.store.data["snapshot"]["days"], [])
        self.assertNotIn("recovery_retries", self.store.data)
        self.assertEqual(self.store.data["checks"], 2)

    def test_success_waits_90_seconds_and_stops_once(self):
        browser = FakeBrowser()
        with patch("visa_tool.app.random.randint") as sample:
            result = self.scheduler(browser, auto_booking=True).run()
        sample.assert_not_called()
        self.assertEqual(result, "success")
        submissions = [args for method, args in browser.calls if method == "submit"]
        self.assertEqual(len(submissions), 1)
        self.assertEqual(submissions[0][0].day, "2026-12-23")
        self.assertEqual(self.clock.elapsed, 90)
        self.assertTrue(self.store.attempt_path.exists())
        self.assertTrue(browser.closed)
        self.assertTrue(any("SUCCESS" in subject for subject, _ in self.messages))
        with self.assertRaises(SafetyError):
            self.scheduler(FakeBrowser(), auto_booking=True).run()

    def test_ambiguous_dispatch_never_retries(self):
        browser = FakeBrowser()
        browser.submission_error = WorkerLost("timeout")
        result = self.scheduler(browser, auto_booking=True).run()
        self.assertEqual(result, "unknown")
        self.assertEqual(sum(method == "submit" for method, _ in browser.calls), 1)
        self.assertNotIn("outcome", [method for method, _ in browser.calls])
        self.assertGreaterEqual(self.clock.elapsed, 90)
        self.assertEqual(self.store.data["booking_result"]["result"], "unknown")

    def test_explicit_rejection_still_stops_after_90_seconds(self):
        browser = FakeBrowser()
        browser.result = "failure"
        result = self.scheduler(browser, auto_booking=True).run()
        self.assertEqual(result, "failure")
        self.assertEqual(sum(method == "submit" for method, _ in browser.calls), 1)
        self.assertGreaterEqual(self.clock.elapsed, 90)

    def test_initial_block_plus_three_retries_then_persistent_stop(self):
        browser = FakeBrowser()
        browser.blocks = 10
        with patch("visa_tool.app.random.randint") as sample:
            result = self.scheduler(browser).run()
        sample.assert_not_called()
        self.assertEqual(result, "error")
        self.assertEqual(sum(method == "load_dates" for method, _ in browser.calls), 4)
        self.assertEqual(self.store.data["recovery_retries"], 3)
        self.assertEqual(self.clock.elapsed, 9)
        self.assertTrue(self.store.data["stopped"])
        self.assertTrue(browser.closed)

    def test_manual_fallback_sends_email_then_stops_on_timeout(self):
        browser = FakeBrowser()
        browser.state = "challenge"
        result = self.scheduler(browser).run()
        self.assertEqual(result, "error")
        self.assertEqual(sum(method == "gui_verify" for method, _ in browser.calls), 1)
        self.assertTrue(any("Manual verification needed" in subject for subject, _ in self.messages))
        self.assertNotIn("submit", [method for method, _ in browser.calls])
        self.assertTrue(browser.closed)

    def test_manual_success_resumes_without_second_auto_attempt(self):
        browser = FakeBrowser()
        scheduler = self.scheduler(browser)
        browser.state = "challenge"
        original_sleep = self.clock.sleep

        def solve(seconds):
            original_sleep(seconds)
            browser.state = "schedule"
        self.clock.sleep = solve
        scheduler.browser = browser
        scheduler._challenge()
        self.assertEqual(scheduler.auto_verifications, 1)
        self.assertTrue(any("Verification resolved" in subject for subject, _ in self.messages))


if __name__ == "__main__":
    unittest.main()
