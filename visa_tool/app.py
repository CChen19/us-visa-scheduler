from __future__ import annotations

import argparse
import uuid
from datetime import date

from .core import (
    BEIJING, SUBMIT_WAIT_SECONDS, AccessBlocked, Clock, InstanceLock, Logs, Mailbox,
    Runtime, SafetyError, Store, WorkerLost, smtp_send,
)


class Scheduler:
    def __init__(self, config, runtime, browser_factory=None):
        if browser_factory is None:
            from .browser import BrowserPort
            browser_factory = BrowserPort
        self.config, self.runtime = config, runtime
        self.store, self.logs = runtime.store, runtime.logs
        self.factory = browser_factory
        self.browser = None
        self.auto_verifications = 0

    def _inspect(self):
        return self.browser.call("inspect")

    def _challenge(self):
        runtime = self.runtime
        if self.store.attempt_path.exists():
            raise SafetyError("Verification is forbidden after a possible submission.")
        if self.config.auto_verification and self.auto_verifications < self.config.max_auto_verifications:
            self.auto_verifications += 1
            runtime.phase = "AUTO_VERIFICATION"
            runtime.logs.event("AUTO_VERIFICATION")
            try:
                attempt = self.browser.call("gui_verify", timeout=30)
            except WorkerLost:
                runtime.notify(
                    "verification-worker-lost:" + uuid.uuid4().hex,
                    "[ACTION REQUIRED] Verification worker stopped",
                    "The automatic verification operation timed out or exited. The run is stopping. "
                    "Use your normal browser for manual verification; no automatic booking will follow.",
                )
                raise
            except SafetyError as exc:
                runtime.logs.event("AUTO_VERIFICATION", "fail", errorDetails=str(exc))
            else:
                deadline = runtime.clock.monotonic() + (60 if attempt.get("attempted") else 0)
                while runtime.clock.monotonic() < deadline:
                    info = self._inspect()
                    if info["state"] in ("login", "questions", "home", "schedule", "queue"):
                        runtime.logs.event("VERIFICATION_RESOLVED")
                        return
                    runtime.wait(1)
        runtime.phase = "MANUAL_VERIFICATION_REQUIRED"
        episode = uuid.uuid4().hex
        deadline = runtime.clock.monotonic() + self.config.manual_timeout
        runtime.logs.event("MANUAL_VERIFICATION_REQUIRED", "paused")
        from datetime import timedelta
        expires = runtime.clock.now() + timedelta(seconds=self.config.manual_timeout)
        runtime.notify(
            "verification:" + episode, "[ACTION REQUIRED] Manual verification needed",
            "Complete only human verification in the existing browser window. "
            "Monitoring and all booking actions are paused. No reschedule was submitted by this run.\n"
            f"Deadline UTC: {expires.isoformat()}\n"
            f"Deadline Beijing: {expires.astimezone(BEIJING).isoformat()}\n"
            "Do not select appointment dates or click Submit. Resume is automatic only after "
            "the expected next page is detected.",
        )
        while runtime.clock.monotonic() < deadline:
            info = self._inspect()
            if info["state"] in ("login", "questions", "home", "schedule", "queue"):
                runtime.logs.event("VERIFICATION_RESOLVED")
                runtime.notify("verification-resolved:" + episode, "Verification resolved",
                               "Expected next page detected. Context will be revalidated.")
                return
            runtime.wait(1)
        raise SafetyError("Manual verification timed out; run stopped.")

    def _reach_schedule(self):
        runtime = self.runtime
        credentials_sent = False
        questions_sent = False
        deadline = runtime.clock.monotonic() + self.config.page_timeout
        while True:
            info = self._inspect()
            state = info["state"]
            if state == "schedule":
                return
            if state == "challenge":
                self._challenge()
                deadline = runtime.clock.monotonic() + self.config.page_timeout
            elif state == "queue":
                runtime.phase = "WAITING_ROOM"
                queue_deadline = runtime.clock.monotonic() + self.config.queue_timeout
                while self._inspect()["state"] == "queue":
                    if runtime.clock.monotonic() >= queue_deadline:
                        raise SafetyError("Waiting-room deadline exceeded.")
                    runtime.wait(2)
                deadline = runtime.clock.monotonic() + self.config.page_timeout
            elif state == "login" and not credentials_sent:
                runtime.phase = "LOGIN"
                day = runtime.clock.now().astimezone(BEIJING).date().isoformat()
                counts = self.store.data.setdefault("login_counts", {})
                # Local conservative budget, not the site's undisclosed daily allowance.
                if counts.get(day, 0) >= 2:
                    raise SafetyError("Local budget of two logins per Beijing day reached.")
                counts[day] = counts.get(day, 0) + 1
                self.store.save()
                credentials_sent = True
                self.browser.call("login")
                deadline = runtime.clock.monotonic() + self.config.page_timeout
            elif state == "questions" and not questions_sent:
                questions_sent = True
                self.browser.call("answer_questions")
                deadline = runtime.clock.monotonic() + self.config.page_timeout
            elif state == "home":
                self.browser.call("enter_reschedule")
                deadline = runtime.clock.monotonic() + self.config.page_timeout
            else:
                if runtime.clock.monotonic() >= deadline:
                    raise SafetyError("Expected login/reschedule transition did not complete.")
                runtime.wait(1)

    def _snapshot(self, values):
        days = {date.fromisoformat(value) for value in values}
        now = self.runtime.clock.now()
        self.store.data["snapshot"] = {
            "observed_utc": now.isoformat(), "days": sorted(day.isoformat() for day in days),
        }
        self.store.save()
        self.logs.write("AvailabilityLog", "AVAILABILITY", dates=[
            {"date": day.isoformat(), "preferred": self.config.priority(day) is not None,
             "priority": self.config.priority(day)}
            for day in sorted(days)
        ], scope="all_dates_in_normal_schedule_day_response")
        if any(self.config.priority(day) for day in days):
            self.runtime.notify(
                "preferred:" + uuid.uuid4().hex, "Preferred dates available",
                self.runtime.snapshot_text(),
            )
        return days

    def _submit_once(self, slot):
        runtime = self.runtime
        self.browser.call("preflight", slot)
        self.store.begin_attempt(slot, runtime.clock.now())
        runtime.notifications_enabled = False
        runtime.phase = "SUBMISSION_OBSERVATION"
        self.logs.write("BookingLog", "SUBMISSION_MAY_HAVE_STARTED", "pending",
                        datetime_beijing=slot.beijing_datetime, slot=slot.__dict__)
        deadline = runtime.clock.monotonic() + SUBMIT_WAIT_SECONDS
        results = set()
        worker_available = True
        try:
            self.browser.call("submit", slot, timeout=min(45, SUBMIT_WAIT_SECONDS))
        except Exception as exc:
            self.logs.event("SUBMIT", "unknown", errorDetails=type(exc).__name__)
            worker_available = not isinstance(exc, WorkerLost)
        while runtime.clock.monotonic() < deadline:
            if worker_available:
                try:
                    result = self.browser.call(
                        "outcome", slot,
                        timeout=max(0.1, min(5, deadline - runtime.clock.monotonic())),
                    )
                    if result in ("success", "failure"):
                        results.add(result)
                except Exception as exc:
                    self.logs.event("RESULT_OBSERVATION", "fail", errorDetails=type(exc).__name__)
                    worker_available = False
            runtime.wait(min(5, max(0, deadline - runtime.clock.monotonic())))
        result = next(iter(results)) if len(results) == 1 else "unknown"
        self.store.data["booking_result"] = {
            "result": result, "datetime_beijing": slot.beijing_datetime,
            "observed_utc": runtime.clock.now().isoformat(),
        }
        self.store.stop("booking_" + result)
        self.logs.write("BookingLog", "BOOKING_RESULT", result,
                        datetime_beijing=slot.beijing_datetime, slot=slot.__dict__)
        runtime.mail.enqueue(
            "booking-result", f"[VisaTool] Rescheduling result: {result.upper()}",
            f"Wuhan {self.config.visa_class}\nRequested Beijing datetime: {slot.beijing_datetime}\n"
            f"Result: {result.upper()}\n"
            "SUCCESS requires the site's AllScheduled response correlated with the requested slot. "
            "UNKNOWN is not a failure confirmation: check the official account manually.\n"
            "No further booking or monitoring will be performed. The permanent submission latch remains.",
        )
        runtime.mail.deliver(final=True)
        return result

    def run(self):
        runtime = self.runtime
        self.store.ensure_runnable()
        try:
            startup_key = "startup:" + uuid.uuid4().hex
            runtime.notify(startup_key, "Monitoring starting",
                           "Notification preflight. The browser has not yet been opened.")
            if not self.store.data["outbox"][startup_key]["sent"]:
                raise SafetyError("Notification preflight failed; refusing unattended website access.")
            self.browser = self.factory(self.config, runtime)
            opened = False
            while self.store.data.get("checks", 0) < self.config.max_checks:
                if self.store.data.get("cooldown_until"):
                    runtime.phase = "COOLDOWN"
                    runtime.wait(max(0, self.store.data["cooldown_until"] - runtime.clock.now().timestamp()))
                    used = self.store.data.get("recovery_retries", 0)
                    if used >= self.config.max_retries:
                        raise SafetyError("Recovery retry budget exhausted.")
                    self.store.data["recovery_retries"] = used + 1
                    self.store.data.pop("cooldown_until")
                    self.store.save()
                    opened = False
                try:
                    if not opened:
                        self.browser.call("open")
                        opened = True
                    self._reach_schedule()
                    runtime.phase = "MONITORING"
                    values = self.browser.call("load_dates")
                    self.store.data["checks"] = self.store.data.get("checks", 0) + 1
                    days = self._snapshot(values)
                    chosen = self.config.choose(
                        day for day in days if day >= runtime.clock.now().astimezone(BEIJING).date()
                    )
                    if self.config.auto_booking and chosen is not None:
                        slot = self.browser.call("prepare_slot", chosen.isoformat())
                        if slot is not None:
                            return self._submit_once(slot)
                        self.logs.event("NO_USABLE_TIME_SLOT")
                except AccessBlocked as exc:
                    if self.store.attempt_path.exists():
                        raise SafetyError("Access restricted after a possible submission; no retry.") from exc
                    used = self.store.data.get("recovery_retries", 0)
                    self.logs.event("ACCESS_RESTRICTED", "fail", retry=used, errorDetails=str(exc))
                    runtime.notify(
                        "blocked:" + uuid.uuid4().hex, "Access restricted",
                        f"Restriction detected; recovery retries used: {used}/{self.config.max_retries}. "
                        "All scheduling requests are paused. No booking submission is retried.",
                    )
                    if used >= self.config.max_retries:
                        raise SafetyError("Still restricted after the recovery retry budget; stopping.")
                    self.store.data["cooldown_until"] = (
                        runtime.clock.now().timestamp() + max(self.config.cooldown_seconds, exc.retry_after)
                    )
                    self.store.save()
                    continue
                runtime.wait(self.config.poll_seconds)
            self.store.stop("check_budget_exhausted")
            runtime.notify("check-budget", "Monitoring stopped",
                           "Configured total check budget reached. No automatic restart is permitted.")
            return "stopped"
        except BaseException as exc:
            # Terminal boundary: never return to monitoring after an unexpected failure.
            reason = str(exc) if isinstance(exc, SafetyError) else type(exc).__name__
            runtime.notifications_enabled = False
            self.store.stop("unexpected_error")
            self.logs.event("TERMINAL_ERROR", "fail", errorDetails=reason)
            if self.store.attempt_path.exists():
                attempted = self.store.data.get("attempt", {}).get("slot", {})
                self.logs.write("BookingLog", "INTERRUPTED_RESULT", "unknown",
                                slot=attempted,
                                errorDetails="Submission may have occurred. Reconcile manually.")
                reason += f"\nRequested slot (Beijing): {attempted.get('day')} {attempted.get('time')}"
            runtime.mail.enqueue(
                "terminal:" + uuid.uuid4().hex, "[VisaTool] Run stopped",
                f"Reason: {reason}\nSubmission latch present: {self.store.attempt_path.exists()}.\n"
                "No automatic restart or further booking is permitted. Check the local logs and account.",
            )
            runtime.mail.deliver(final=True)
            return "error"
        finally:
            runtime.notifications_enabled = False
            if self.browser is not None:
                try:
                    self.browser.close()
                except Exception as exc:
                    self.store.stop("browser_cleanup_failed")
                    self.logs.event("BROWSER_CLOSE", "fail", errorDetails=type(exc).__name__)
                    runtime.mail.enqueue(
                        "cleanup-error", "[VisaTool] Browser cleanup failed",
                        "The task is stopped, but closing the owned browser could not be confirmed. "
                        "Inspect that browser manually. Do not retry a possible submission.",
                    )
                    runtime.mail.deliver(final=True)
                    raise SafetyError("Browser cleanup could not be confirmed.") from exc
            runtime.mail.deliver(final=True)
            self.logs.event("PROCESS_STOP")


def cli(config_factory):
    parser = argparse.ArgumentParser(description="Wuhan visitor visa monitoring and one-shot rescheduling.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-config", action="store_true", help="Validate configuration offline.")
    modes.add_argument("--test-email", action="store_true", help="Send one explicit SMTP test; no browser.")
    modes.add_argument("--send-pending", action="store_true", help="Retry saved mail only; no browser.")
    args = parser.parse_args()
    logs = None
    try:
        config = config_factory()
        config.validate(browser=not (args.test_email or args.send_pending))
        if args.check_config:
            print("Configuration is valid. No network request or browser action was performed.")
            return 0
        if args.test_email:
            smtp_send(config, "[VisaTool] Notification test", "Explicit notification test; no visa-site access.")
            print("SMTP server accepted the test message.")
            return 0
        with InstanceLock(config.root):
            clock = Clock()
            store = Store(config.root)
            logs = Logs(config.root, clock)
            mail = Mailbox(config, store, logs, clock)
            if args.send_pending:
                for item in store.data.get("outbox", {}).values():
                    if not item["sent"]:
                        item["attempts"], item["next_try"] = 0, 0
                store.save()
                mail.deliver(final=True)
                return 1 if any(not item["sent"] for item in store.data.get("outbox", {}).values()) else 0
            runtime = Runtime(config, store, logs, mail, clock)
            result = Scheduler(config, runtime).run()
            unsent = any(not item["sent"] for item in store.data.get("outbox", {}).values())
            return 1 if result in ("error", "unknown", "failure") or unsent else 0
    except (SafetyError, ValueError, OSError) as exc:
        print(str(exc) if isinstance(exc, SafetyError) else f"Startup failed: {type(exc).__name__}")
        return 2
    finally:
        if logs is not None:
            logs.close()
