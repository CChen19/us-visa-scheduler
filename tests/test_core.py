import json
import smtplib
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from visa_tool.core import (
    BEIJING, Config, InstanceLock, Logs, Mailbox, Runtime, SafetyError, Slot, Store, parse_day,
)


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 11, 14, 23, 59, tzinfo=timezone.utc)
        self.elapsed = 0

    def now(self):
        return self.value

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds
        self.value += timedelta(seconds=seconds)


def configuration(root, **changes):
    base = Config(
        root=Path(root), username="test-user", password="not-a-secret",
        applicants=("Test Applicant",), existing_appointment="2027-02-03T09:00:00",
        security_answers={"Question One?": "one", "Question Two?": "two", "Question Three?": "three"},
        ranges=(("2026-12-22", "2026-12-25"), ("2026-12-01", "2026-12-15")),
        smtp_host="smtp.invalid", smtp_username="sender@example.invalid",
        smtp_password="not-a-secret", email_to="recipient@example.invalid",
        poll_seconds=1, cooldown_seconds=2, max_checks=2, manual_timeout=2, page_timeout=2,
    )
    return replace(base, **changes)


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = configuration(self.temp.name)
        self.clock = FakeClock()
        self.store = Store(self.config.root)
        self.logs = Logs(self.config.root, self.clock)
        self.messages = []
        self.mail = Mailbox(self.config, self.store, self.logs, self.clock,
                            lambda config, subject, body: self.messages.append((subject, body)))
        self.runtime = Runtime(self.config, self.store, self.logs, self.mail, self.clock)

    def tearDown(self):
        self.logs.close()
        self.temp.cleanup()


class CoreTests(RuntimeTest):
    def test_priority_gaps_boundaries_and_no_match(self):
        self.config.validate(today=date(2026, 11, 15))
        days = {date(2026, 12, day) for day in (1, 15, 16, 22, 23, 25)}
        self.assertEqual(self.config.choose(days), date(2026, 12, 22))
        self.assertIsNone(self.config.priority(date(2026, 12, 16)))
        self.assertEqual(self.config.priority(date(2026, 12, 15)), 2)
        self.assertIsNone(self.config.choose({date(2027, 1, 1)}))
        self.assertEqual(self.config.choose(day for day in [date(2026, 12, 1)]), date(2026, 12, 1))

    def test_bad_config_fails_before_network(self):
        for config in (replace(self.config, max_retries=4),
                       replace(self.config, security_answers={}),
                       replace(self.config, ranges=(("2026-12-25", "2026-12-01"),)),
                       replace(self.config, daily_times=("08:00", "08:00", "17:00"))):
            with self.subTest(config=config):
                with self.assertRaises(SafetyError):
                    config.validate(today=date(2026, 11, 15))

    def test_parse_rejects_partial_or_bad_dates(self):
        self.assertEqual(parse_day("2027/8/3"), date(2027, 8, 3))
        self.assertEqual(parse_day("2026-12-01T00:00:00Z"), date(2026, 12, 1))
        for value in ("not a date", "2026-02-30", "2026-12-01junk"):
            with self.assertRaises(SafetyError):
                parse_day(value)

    def test_submission_latch_survives_restart_and_duplicate_dispatch(self):
        slot = Slot("2026-12-01", "08:30", "2", 7)
        self.store.begin_attempt(slot, self.clock.now())
        resumed = Store(self.config.root)
        with self.assertRaises(SafetyError):
            resumed.ensure_runnable()
        with self.assertRaises(SafetyError):
            resumed.begin_attempt(slot, self.clock.now())
        self.assertEqual(json.loads(resumed.attempt_path.read_text())["slot"]["time"], "08:30")

    def test_corrupt_state_and_empty_attempt_fail_closed(self):
        self.store.path.write_text("{", encoding="utf-8")
        with self.assertRaises(SafetyError):
            Store(self.config.root)
        self.store.path.unlink()
        self.store.attempt_path.touch()
        with self.assertRaises(SafetyError):
            Store(self.config.root).ensure_runnable()

    def test_single_instance_exclusion(self):
        with InstanceLock(self.config.root):
            with self.assertRaises(SafetyError):
                with InstanceLock(self.config.root):
                    self.fail("Second lock was allowed.")
        with InstanceLock(self.config.root):
            pass

    def test_logs_rotate_beijing_midnight_and_restart_never_overwrites(self):
        self.clock.value = datetime(2026, 11, 15, 15, 59, 59, tzinfo=timezone.utc)
        self.logs.event("BEFORE")
        old = sorted((self.config.root / "logs").glob("EventLog_2026_11_15*"))
        self.clock.sleep(2)
        self.runtime.tick()
        self.logs.event("AFTER")
        restarted = Logs(self.config.root, self.clock)
        restarted.event("RESTART")
        restarted.close()
        paths = list((self.config.root / "logs").glob("EventLog_2026_11_16*"))
        self.assertEqual(len(paths), 2)
        self.assertIn("BEFORE", old[0].read_text())
        records = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
        self.assertTrue(any(row["action"] == "AFTER" for row in records))
        self.assertTrue(all(row["timestamp_beijing"].endswith("+08:00") for row in records))
        self.assertTrue(all(row["timestamp_pst"].endswith("-08:00") for row in records))

    def test_mail_retry_is_durable_and_bounded(self):
        def broken(*_):
            raise smtplib.SMTPException("sensitive exception must not be logged")
        self.mail.sender = broken
        self.mail.enqueue("one", "Notification", "Body")
        self.mail.deliver(final=True)
        self.assertEqual(self.store.data["outbox"]["one"]["attempts"], 3)
        self.mail.deliver(final=True)
        self.assertEqual(self.store.data["outbox"]["one"]["attempts"], 3)
        self.assertFalse(Store(self.config.root).data["outbox"]["one"]["sent"])
        contents = "".join(path.read_text() for path in (self.config.root / "logs").glob("*"))
        self.assertNotIn("sensitive exception", contents)

    def test_daily_email_slots_dedupe_and_catch_up_without_browser(self):
        for hour in (8, 12, 17):
            self.clock.value = datetime(2026, 11, 15, hour, 0, tzinfo=BEIJING)
            self.runtime.tick()
            self.runtime.tick()
        self.assertEqual(len(self.messages), 3)
        self.assertEqual(len(Store(self.config.root).data["daily_done"]), 3)
        self.clock.value += timedelta(days=1, hours=2)
        self.runtime.tick()
        self.assertEqual(len(self.messages), 4)  # Only latest missed slot is caught up.


if __name__ == "__main__":
    unittest.main()
