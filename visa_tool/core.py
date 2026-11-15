from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8), "Beijing")
PST = timezone(timedelta(hours=-8), "PST")
POST_ID = "7b6af614-b0db-ec11-a7b4-001dd80234f6"
SUBMIT_WAIT_SECONDS = 90


class SafetyError(Exception):
    """Only non-sensitive, operator-facing messages belong in this exception."""


class AccessBlocked(SafetyError):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


class WorkerLost(SafetyError):
    pass


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def parse_day(value):
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T\s]|$)", str(value))
    if not match:
        raise SafetyError("Unrecognized calendar date; snapshot rejected.")
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise SafetyError("Invalid calendar date; snapshot rejected.") from exc


def parse_local_datetime(value):
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SafetyError("Existing appointment must be an ISO local datetime.") from exc
    if result.tzinfo is not None:
        result = result.astimezone(BEIJING).replace(tzinfo=None)
    return result


@dataclass
class Config:
    root: Path
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    security_answers: dict = field(default_factory=dict, repr=False)
    applicants: tuple = field(default=(), repr=False)
    visa_class: str = "B1/B2"
    existing_appointment: str = ""
    ranges: tuple = ()
    auto_booking: bool = False
    daily_times: tuple = ("08:00", "12:00", "17:00")
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)
    email_to: str = field(default="", repr=False)
    poll_seconds_min: int = 1800
    poll_seconds_max: int = 3600
    cooldown_seconds: int = 86400
    max_retries: int = 3
    max_checks: int = 24
    auto_verification: bool = True
    max_auto_verifications: int = 1
    manual_timeout: int = 900
    page_timeout: int = 45
    queue_timeout: int = 1800

    def validate(self, browser=True, today=None):
        for value in (self.smtp_host, self.smtp_username, self.smtp_password, self.email_to):
            if not value or "\n" in value or "\r" in value:
                raise SafetyError("Configure SMTP host, username, app password and recipient.")
        if not 1 <= self.smtp_port <= 65535:
            raise SafetyError("Invalid SMTP port.")
        if not browser:
            return
        if not self.username or not self.password:
            raise SafetyError("Set VISA_USERNAME and VISA_PASSWORD.")
        if not self.applicants or any(not normalized(name) for name in self.applicants):
            raise SafetyError("Set the exact expected applicant names (VISA_APPLICANTS).")
        if len(set(map(normalized, self.applicants))) != len(self.applicants):
            raise SafetyError("Expected applicant names must be distinct.")
        if self.visa_class not in ("B1/B2", "B2"):
            raise SafetyError("This tool supports the configured visitor visa class only.")
        if not self.existing_appointment:
            raise SafetyError("Set VISA_EXISTING_APPOINTMENT to your current Beijing appointment.")
        existing = parse_local_datetime(self.existing_appointment)
        today = today or datetime.now(BEIJING).date()
        if existing.date() <= today:
            raise SafetyError("Existing appointment must be after today in Beijing.")
        if not self.ranges:
            raise SafetyError("Configure at least one preferred range.")
        for start, end in self.ranges:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first > last or first < today:
                raise SafetyError("Preferred ranges must be ordered internally and not in the past.")
        if len(self.daily_times) != 3 or len(set(self.daily_times)) != 3:
            raise SafetyError("Configure exactly three distinct daily notification times.")
        for value in self.daily_times:
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise SafetyError("Daily times must be HH:MM in Beijing time.")
        if not 0 <= self.max_retries <= 3:
            raise SafetyError("Recovery retries must be between zero and three.")
        if (type(self.poll_seconds_min) is not int or type(self.poll_seconds_max) is not int
                or not 0 < self.poll_seconds_min <= self.poll_seconds_max):
            raise SafetyError("Polling bounds must be positive integers with POLL_SECONDS_MIN <= POLL_SECONDS_MAX.")
        if min(self.cooldown_seconds, self.max_checks,
               self.manual_timeout, self.page_timeout, self.queue_timeout) <= 0:
            raise SafetyError("Timeouts and operational budgets must be positive.")
        if not 0 <= self.max_auto_verifications <= 1:
            raise SafetyError("At most one automatic verification invocation is allowed.")
        keys = [normalized(key) for key in self.security_answers]
        if len(keys) < 3 or len(keys) != len(set(keys)) or any(not key for key in keys):
            raise SafetyError("Security questions must have distinct, nonempty normalized text.")
        if any(not answer for answer in self.security_answers.values()):
            raise SafetyError("Every configured security question needs a nonempty answer.")

    def priority(self, day):
        for index, (start, end) in enumerate(self.ranges):
            if date.fromisoformat(start) <= day <= date.fromisoformat(end):
                return index + 1
        return None

    def choose(self, days):
        days = tuple(days)
        for start, end in self.ranges:
            matches = sorted(day for day in days
                             if date.fromisoformat(start) <= day <= date.fromisoformat(end))
            if matches:
                return matches[0]
        return None


@dataclass(frozen=True)
class Slot:
    day: str
    time: str
    entry: str
    capacity: int

    @property
    def beijing_datetime(self):
        return f"{self.day}T{self.time}:00+08:00"


class Clock:
    def now(self):
        return datetime.now(UTC)

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


def atomic_json(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class InstanceLock:
    def __init__(self, root):
        self.path = root / ".state" / "instance.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise SafetyError("Another instance owns this project. No browser was opened.") from exc
        return self

    def __exit__(self, *_):
        self.handle.close()


class Store:
    def __init__(self, root):
        self.directory = root / ".state"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.attempt_path = self.directory / "submission.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (ValueError, OSError) as exc:
            raise SafetyError("Persistent state is unreadable. Stop and reconcile manually.") from exc
        if not isinstance(self.data, dict):
            raise SafetyError("Persistent state is invalid.")

    def save(self):
        atomic_json(self.path, self.data)

    def ensure_runnable(self):
        if self.attempt_path.exists():
            raise SafetyError("A submission may already have occurred. Manual reconciliation required.")
        if self.data.get("stopped"):
            raise SafetyError("This task is persistently stopped. Review .state before any new run.")

    def begin_attempt(self, slot, now):
        record = {"status": "submission_may_have_started", "slot": slot.__dict__,
                  "timestamp_utc": now.isoformat()}
        try:
            with self.attempt_path.open("x", encoding="utf-8") as handle:
                json.dump(record, handle)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise SafetyError("Submission latch already exists; no second Submit is allowed.") from exc
        self.data["stopped"] = "submission_may_have_started"
        self.data["attempt"] = record
        self.save()

    def stop(self, reason):
        self.data["stopped"] = reason
        self.save()


class Logs:
    streams = ("EventLog", "AvailabilityLog", "BookingLog")

    def __init__(self, root, clock):
        self.folder = root / "logs"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.run_id = uuid.uuid4().hex[:12]
        self.day = None
        self.handles = {}
        self.rotate()

    def rotate(self):
        day = self.clock.now().astimezone(BEIJING).strftime("%Y_%m_%d")
        if day == self.day:
            return
        self.close()
        for stream in self.streams:
            path = self.folder / f"{stream}_{day}.jsonl"
            try:
                handle = path.open("x", encoding="utf-8")
            except FileExistsError:
                path = self.folder / f"{stream}_{day}_{uuid.uuid4().hex}.jsonl"
                handle = path.open("x", encoding="utf-8")
            self.handles[stream] = handle
        self.day = day

    def write(self, stream, action, result="success", **details):
        self.rotate()
        now = self.clock.now()
        record = {
            "timestamp_pst": now.astimezone(PST).isoformat(),
            "timestamp_utc": now.astimezone(UTC).isoformat(),
            "timestamp_beijing": now.astimezone(BEIJING).isoformat(),
            "run_id": self.run_id, "action": action, "result": result,
            "errorDetails": details.pop("errorDetails", None), **details,
        }
        handle = self.handles[stream]
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        if stream == "BookingLog":
            os.fsync(handle.fileno())

    def event(self, action, result="success", **details):
        self.write("EventLog", action, result, **details)

    def close(self):
        for handle in self.handles.values():
            handle.close()
        self.handles = {}


def smtp_send(config, subject, body):
    message = EmailMessage()
    message["From"] = config.smtp_username
    message["To"] = config.email_to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=10,
                          context=ssl.create_default_context()) as server:
        server.login(config.smtp_username, config.smtp_password)
        refused = server.send_message(message)
        if refused:
            raise SafetyError("SMTP recipient rejected.")


class Mailbox:
    def __init__(self, config, store, logs, clock, sender=smtp_send):
        self.config, self.store, self.logs, self.clock = config, store, logs, clock
        self.sender = sender

    def enqueue(self, key, subject, body):
        outbox = self.store.data.setdefault("outbox", {})
        if key not in outbox:
            outbox[key] = {"subject": subject, "body": body, "attempts": 0,
                           "sent": False, "next_try": 0}
            self.store.save()

    def deliver(self, final=False):
        for item in self.store.data.get("outbox", {}).values():
            while not item["sent"] and item["attempts"] < 3:
                if not final and item["next_try"] > self.clock.now().timestamp():
                    break
                item["attempts"] += 1
                # Persist intent before network I/O; sending is at-least-once after crashes.
                self.store.save()
                try:
                    self.sender(self.config, item["subject"], item["body"])
                except (OSError, smtplib.SMTPException, SafetyError) as exc:
                    item["next_try"] = self.clock.now().timestamp() + 60
                    self.logs.event("EMAIL_SEND", "fail", errorDetails=type(exc).__name__)
                    print("Email delivery failed; notice remains in .state/state.json.", flush=True)
                else:
                    item["sent"] = True
                    self.logs.event("EMAIL_SEND")
                self.store.save()
                if not final:
                    break


class Runtime:
    def __init__(self, config, store, logs, mail, clock):
        self.config, self.store, self.logs, self.mail, self.clock = config, store, logs, mail, clock
        self.phase = "STARTING"
        self.notifications_enabled = True

    def snapshot_text(self):
        snapshot = self.store.data.get("snapshot")
        if not snapshot:
            return f"State: {self.phase}. No successful availability observation yet."
        rows = [f"State: {self.phase}; Wuhan {self.config.visa_class}",
                f"Observed (UTC): {snapshot['observed_utc']}",
                "Scope: all dates in the last normal schedule-day response; not a current guarantee."]
        for value in snapshot["days"]:
            rank = self.config.priority(date.fromisoformat(value))
            rows.append(f"{value}" + (f"  [PREFERRED priority {rank}]" if rank else ""))
        if not snapshot["days"]:
            rows.append("No days returned in that observation.")
        return "\n".join(rows)

    def tick(self):
        self.logs.rotate()
        if not self.notifications_enabled:
            return
        now = self.clock.now().astimezone(BEIJING)
        due = [value for value in sorted(self.config.daily_times)
               if value <= now.strftime("%H:%M")]
        done = self.store.data.setdefault("daily_done", [])
        missing = [value for value in due if f"{now.date()}:{value}" not in done]
        if missing:
            slot = missing[-1]
            key = f"daily:{now.date()}:{slot}"
            self.mail.enqueue(key, "[VisaTool] Daily availability",
                              f"Beijing scheduled time: {now.date()} {slot}\n"
                              + self.snapshot_text())
            done.extend(f"{now.date()}:{value}" for value in missing)
            self.store.data["daily_done"] = done[-30:]
            self.store.save()
        self.mail.deliver()

    def wait(self, seconds):
        deadline = self.clock.monotonic() + max(0, seconds)
        while self.clock.monotonic() < deadline:
            self.tick()
            self.clock.sleep(min(1, deadline - self.clock.monotonic()))

    def notify(self, key, subject, body):
        self.mail.enqueue(key, "[VisaTool] " + subject, body)
        self.mail.deliver()
