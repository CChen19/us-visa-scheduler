# Wuhan visitor visa appointment assistant

[简体中文](README.zh-CN.md)

A local Windows application for monitoring the normal U.S. visa scheduling UI and
optionally making **one guarded rescheduling submission** for an existing Wuhan
B1/B2 or B2 appointment. Monitoring is the default; booking must be enabled explicitly.

**Important:** the saved sign-in page prohibits automated access. Use automation
only where permitted by the service provider. No polling interval, browser mode,
or CAPTCHA helper guarantees access or prevents account restrictions.

## What it does

- Enters through `https://www.usvisascheduling.com/zh-CN/SignIn`, fills credentials
  only on the expected authentication host, and matches the displayed security
  questions to locally configured answers (including question pairs 1/3 and 2/3).
- Attempts one compatible SeleniumBase UC GUI verification interaction on an
  authentication page. If unsuccessful or unsafe, emails you and pauses for
  manual verification in the same browser. It never uses an external CAPTCHA solver.
- On the home page, clicks **only** the validated reschedule link. Checks the
  displayed visa class/post, exact applicant group, and existing appointment.
- Observes all dates returned by the site's normal schedule-day request, not just
  the two visible calendar months. No direct/private API replay is used.
- Applies inclusive date ranges in your priority order; within the first matching
  range, chooses the earliest date. Gaps between ranges remain excluded.
- In monitor-only mode, never clicks a day, time slot, or Submit. In booking mode,
  selects only a preferred day and the first enabled time row with sufficient
  capacity, then revalidates the complete selection.
- Writes a permanent submission latch **before** dispatching Submit. Observes the
  result for 90 seconds, sends an outcome email, closes the owned browser, and stops.
  Exceptions, timeouts, email failures, or restarts do not authorize a second submission.
- Handles detected access restrictions with persisted cooldowns and at most three
  recovery retries after the initial failure. Other unexpected errors stop the task.
- Writes daily structured event, availability, and booking logs. Sends preferred
  availability, daily status, manual-verification, restriction, error, and booking emails.

**Not guaranteed:** successful CAPTCHA completion; no account block; server-side
rescheduling quota accounting; atomic replacement/preservation of the old appointment;
slot availability until submission; or email inbox delivery. Unknown booking outcomes
are reported as **UNKNOWN**, never assumed failed or successful.

## Requirements and installation (Windows PowerShell)

Use an interactive Windows desktop with Google Chrome installed. The validated
environment used Python 3.9.13; Python 3.9+ is supported by the pinned SeleniumBase
release. Do not silently upgrade dependencies during a monitored run.

```powershell
Set-Location C:\src\USVisa\us-visa-scheduler
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Activation is unnecessary. Requirements pin SeleniumBase 4.51.12 with its
PyAutoGUI extra, and PyAutoGUI 0.9.54. SeleniumBase resolves the Selenium version
for your Python version and may download a matching ChromeDriver on first use.
Installation and driver provisioning require network access.

## Configure the beginning of `main.py`

All operational settings are in its opening section. Do not put real secrets in
tracked source files. Environment variables are local process inputs, **not an
encrypted secret vault**; protect the machine/session and local files.

### Authentication, identity, and existing appointment

```powershell
$env:VISA_USERNAME = 'your-username'
$env:VISA_PASSWORD = [System.Net.NetworkCredential]::new(
    '', (Read-Host 'Visa password' -AsSecureString)).Password
$env:VISA_APPLICANTS = 'Exact displayed applicant name'
# Multiple applicants: exact names separated with semicolons.
$env:VISA_EXISTING_APPOINTMENT = '2030-01-15T10:00:00'
```

Replace the example timestamp with your **actual current appointment**, in Beijing
time. It is an identity/safety check, not a target date. The tool expects one
consular appointment entry; unsupported group/appointment layouts fail closed.
Set `EXPECTED_VISA_CLASS` to the exact home-page value (`B1/B2` or `B2`).

Provide the three real question texts and answer references at the top of `main.py`:

```python
SECURITY_ANSWERS = {
    "Exact question text 1": os.environ.get("VISA_ANSWER_1", ""),
    "Exact question text 2": os.environ.get("VISA_ANSWER_2", ""),
    "Exact question text 3": os.environ.get("VISA_ANSWER_3", ""),
}
```

Set those environment variables locally, preferably using the same masked
`Read-Host` pattern as the password. Use the exact displayed Chinese/English text;
explicit language aliases are allowed. Whitespace/Unicode normalization is applied,
but unrelated questions are never guessed. Missing/unknown questions stop the run.
There is a persisted **local cap of two credential submissions per Beijing day**;
this is not a claim about the website's undisclosed daily allowance.

### Preferences and notifications

```python
PREFERRED_DATE_RANGES = [
    ("2026-12-01", "2026-12-15"),
    ("2026-12-22", "2026-12-25"),
]
AUTO_BOOKING = False
DAILY_EMAIL_TIMES = ["08:00", "12:00", "17:00"]
```

Replace expired examples before running. Startup rejects reversed/past ranges;
during monitoring, past days are not chosen. Put the most important range first,
even if it is later chronologically. Monitor-only mode still sends preferred-date
emails. There is no selection-based "dry run": disabled booking is truly no-click.

```powershell
$env:VISA_EMAIL_SENDER = 'your-address@example.com'
$env:VISA_EMAIL_TO = 'destination@example.com'
$env:VISA_SMTP_PASSWORD = [System.Net.NetworkCredential]::new(
    '', (Read-Host 'SMTP app password' -AsSecureString)).Password
```

Set `SMTP_HOST`/`SMTP_PORT` in `main.py`. The implemented transport is
**implicit TLS SMTP**, normally port 465, not STARTTLS/port 587. Use your provider's
SMTP app password. A notification-preflight email must be accepted before the
browser opens. SMTP acceptance does not guarantee delivery to the inbox.

### Operational budgets

| Setting | Default | Meaning |
|---|---:|---|
| `POLL_SECONDS` | 1800 | Delay between observations; not a known safe website rate. |
| `BLOCK_COOLDOWN_SECONDS` | 86400 | Local fallback wait after a detected restriction. Longer `Retry-After` values prevail. Not a verified unblock period. |
| `MAX_RECOVERY_RETRIES` | 3 | Initial failure plus at most three recovery cycles, persisted across restarts. |
| `MAX_CHECKS_PER_RUN` | 24 | Maximum successful checks for this persistent task. Despite the historical name, restarts do not reset its count. |
| `AUTO_VERIFICATION` | `True` | Best-effort GUI helper; no automatic challenge guarantee. |
| `MAX_AUTO_VERIFICATIONS` | 1 | At most one helper invocation per process run, not necessarily one library-internal click/request. |
| `MANUAL_VERIFICATION_TIMEOUT_SECONDS` | 900 | Bounded manual handoff; automatic notification, pause, then resume or stop. |
| `PAGE_TIMEOUT_SECONDS` | 45 | Normal page/request wait. |
| `QUEUE_TIMEOUT_SECONDS` | 1800 | Maximum wait for the site's own virtual queue. |

Detected page-view warnings are treated as restrictions. A cooldown performs no
scheduling queries; it does not erase server-side account state. A recovery cycle
may include normal navigation/authentication requests. The maximum is not three
requests at HTTP level. The local check/login budgets can stop the task earlier.

## Run

```powershell
.\.venv\Scripts\python.exe main.py --check-config
.\.venv\Scripts\python.exe main.py --test-email
.\.venv\Scripts\python.exe main.py
```

`--check-config` is offline and does not create state or open a browser.
`--test-email` sends one explicit email and never accesses the visa site.
The normal command first validates configuration, acquires the project instance
lock, checks durable stop/latch state, and tests email delivery.

Start with `AUTO_BOOKING = False`. Set it to `True` only after reviewing the
configuration, permission to automate, and the risks/limits above. No live booking
is needed to run the included tests.

The browser is forced to `Asia/Shanghai` time and checked before selections.
Keep it visible for GUI verification; do not minimize it or compete for input.
An unsafe/obscured/non-foreground window requires manual fallback. The helper is
limited to authentication pages, not an already selected booking screen.

If manual verification is requested, complete only verification in the existing
window. The tool pauses browser actions, emails a deadline, and resumes only when
an expected next state is detected. Do not change appointments or click Submit
yourself in that window. If the helper hangs, its owned process tree is terminated
and the run stops; that case cannot preserve the browser for same-window fallback.

The process owns an isolated browser worker. On Windows, a kill-on-close Job
Object contains that worker and its Chrome descendants; unrelated Chrome windows
are not targeted. If the task is run as a scheduled job, it needs an interactive,
unlocked user session for GUI operations.

## Workflow and result semantics

```text
Validate -> instance lock + durable state -> email preflight -> sign in
  -> automatic verification / emailed manual fallback -> answer questions
  -> validate home -> only Reschedule -> validate existing booking and applicants
  -> normal post selection -> observe complete returned dates -> logs + emails
  -> no match / monitor-only: wait and repeat within budgets
  -> preferred day: choose first usable time row -> final validation
  -> persist submission latch -> Submit once -> observe 90 seconds
  -> SUCCESS / FAILURE / UNKNOWN -> outcome email -> close browser -> stop
```

SUCCESS requires the site's `AllScheduled` response correlated with the requested
date/entry, not merely a title/redirect. FAILURE requires an explicit error
response. Missing/conflicting evidence is UNKNOWN. A stale selection or changed
application stops before dispatch. If the preferred day has no usable time row,
that observation makes no submission; monitoring continues.

After Submit might have started, there is **no** challenge handling, refresh,
reauthentication, cancellation, or submission retry. The site can perform its own
normal callbacks; the client does not retry the booking. An explicit Ctrl+C or
process/machine failure can interrupt the 90-second wait; the persistent latch
still prevents another attempt.

## Logs, persistence, and email recovery

All paths are relative to the project root, not your shell's current directory:

- `logs\EventLog_YYYY_MM_DD.jsonl`: operations, outcomes, errors, retries.
- `logs\AvailabilityLog_YYYY_MM_DD.jsonl`: every successful full response snapshot,
  including unpreferred dates and explicit preferred/range-rank fields.
- `logs\BookingLog_YYYY_MM_DD.jsonl`: attempted Beijing date/time and result.
- `.state\state.json`: stop/cooldown/retry counters, last snapshot, daily scheduling,
  and durable email outbox. Does not store visa credentials or security answers.
- `.state\submission.json`: exclusively created, fsynced, permanent submission latch.

Every log record has PST (fixed UTC-08:00), UTC, and Beijing timestamps. Files
rotate on the Beijing date boundary, including during waits. A same-day restart
creates new files with unique suffixes; nothing is truncated. Rotation runs on the
event-loop heartbeat, not a hard real-time midnight guarantee.

Preferred-availability email is sent on **each successful observation containing
preferred dates**, even when unchanged. Daily emails use the latest cached snapshot
and identify its age/state, not a new forced check. If multiple daily slots were
missed before startup, only the most recent missed slot is sent; already-recorded
slots are not duplicated on restart. Daily emails stop with the task.

Mail has three bounded delivery attempts and a persistent unsent record. To retry
only unsent notices, without opening a browser or touching the appointment:

```powershell
.\.venv\Scripts\python.exe main.py --send-pending
```

This command explicitly resets only unsent email attempt counts. Email may be
duplicated after a crash between SMTP acceptance and local acknowledgement.
Email failure never clears the booking latch or restarts booking.

**Do not delete `.state` to bypass a stopped task.** After a submission, manually
reconcile the account's actual appointment and remaining allowance first. Starting
a genuinely new task requires a deliberate offline archive/reset of its state
after reconciliation and updated configuration; there is no automatic reset
command. Stop all instances before managing these files. Keep only one project
copy per account: the local lock cannot coordinate separate machines/copies.

Protect local state/logs and browser profiles as personal information. `.gitignore`
excludes state, logs, the virtualenv, and SeleniumBase test artifacts. Do not upload
authenticated page dumps or secret-bearing screenshots.

Exit codes: `0` completed normally/stopped at the check budget; `1` error,
failed/unknown booking, or unsent notifications; `2` configuration/startup/refused
restart. A zero exit code alone is not booking confirmation.

## Verification and limitations

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# Optional real Chrome tests, using synthetic loopback pages only:
$env:VISA_LOCAL_BROWSER_TESTS = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:\VISA_LOCAL_BROWSER_TESTS
```

The opt-in suite blocks visa/auth/Cloudflare domains in its fixture browser. A
separate UC smoke test opens only the default blank page and verifies owned-worker
teardown. Tests cover range gaps/priority, all question pairs, nonpreferred no-click,
empty results, all returned months, actual slot times, stale contexts, at-most-once
dispatch/restart, 90-second outcomes, retry caps, mail failure, and date rotation.

Live account login, actual Cloudflare acceptance, quota consumption, and a real
rescheduling transaction have **not** been validated. Site markup/response changes
fail closed rather than inventing availability or success. General new bookings,
other consulates, OFC/biometrics scheduling, cancellation, and application replacement
are not supported.

The source/resource analysis and detailed requirement assessment are in
`C:\src\USVisa\CopilotOutput\VisaTool_analysis_astra.md`.

Original project acknowledgement: `SYuan03/VisaAppointmentWatcher` and the
`CChen19/us-visa-scheduler` draft.
