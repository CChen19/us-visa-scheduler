"""Configure here, then run: python main.py --check-config."""
import os
from pathlib import Path

# Environment values override the fallbacks below only when the variable exists.
# A present-but-empty/invalid value is NOT replaced by a fallback.
# Conversion is deferred to build_config(), so --help works even with bad settings.
# Use environment variables for secrets; never commit real passwords/answers.
# Example VISA_USERNAME: "your-visa-username".
USERNAME = os.environ.get("VISA_USERNAME", "")
# Example VISA_PASSWORD: "your-visa-password" (replace locally; not a Gmail app password).
PASSWORD = os.environ.get("VISA_PASSWORD", "")
# VISA_SECURITY_ANSWERS is a JSON object; it replaces, rather than merges, the map.
# Example: {"Exact question 1":"answer1","Exact question 2":"answer2","Exact question 3":"answer3"}
SECURITY_ANSWERS = os.environ.get("VISA_SECURITY_ANSWERS", {
    # Fallback map: exact displayed Chinese/English question text -> answer.
    # "Your configured question": os.environ.get("VISA_ANSWER_1", ""),
})
# Example VISA_APPLICANTS: "Exact Applicant One;Exact Applicant Two".
EXPECTED_APPLICANTS = [
    name.strip() for name in os.environ.get("VISA_APPLICANTS", "").split(";")
    if name.strip()
]
# Example VISA_VISA_CLASS: "B1/B2" or "B2", exactly as shown on the home page.
EXPECTED_VISA_CLASS = os.environ.get("VISA_VISA_CLASS", "B1/B2")
# VISA_EXISTING_APPOINTMENT: current appointment, NOT the desired target.
# Example: "2030-01-15T10:00:00", a Beijing-local ISO datetime.
EXPECTED_EXISTING_APPOINTMENT = os.environ.get("VISA_EXISTING_APPOINTMENT", "")
# VISA_PREFERRED_DATE_RANGES: JSON array of inclusive ranges, highest priority first.
# Example: [["2026-12-01","2026-12-15"],["2026-12-22","2026-12-25"]]
PREFERRED_DATE_RANGES = os.environ.get("VISA_PREFERRED_DATE_RANGES", [
    ("2026-12-01", "2026-12-15"),
    ("2026-12-22", "2026-12-25"),
])
# VISA_AUTO_BOOKING: "true"/"false" or "1"/"0" (case-insensitive).
# Example "false": no date, time-slot, or Submit clicks. Invalid strings fail closed.
AUTO_BOOKING = os.environ.get("VISA_AUTO_BOOKING", False)
# VISA_DAILY_EMAIL_TIMES: JSON array of exactly three Beijing (UTC+08:00) times.
# Example: ["08:00","12:00","17:00"]
DAILY_EMAIL_TIMES = os.environ.get("VISA_DAILY_EMAIL_TIMES", ["08:00", "12:00", "17:00"])

# Example VISA_SMTP_HOST: "smtp.gmail.com"; Gmail is the fallback provider.
SMTP_HOST = os.environ.get("VISA_SMTP_HOST", "smtp.gmail.com")
# Example VISA_SMTP_PORT: "465" (implicit TLS). STARTTLS/587 is not implemented.
SMTP_PORT = os.environ.get("VISA_SMTP_PORT", 465)
# Example VISA_EMAIL_SENDER: "your.name@gmail.com" (full Gmail login and From address).
SMTP_USERNAME = os.environ.get("VISA_EMAIL_SENDER", "")
# VISA_SMTP_PASSWORD: Google app password; format example only: "abcdefghijklmnop".
# Do not use your normal Google password or the visa-account password.
SMTP_PASSWORD = os.environ.get("VISA_SMTP_PASSWORD", "")
# Example VISA_EMAIL_TO: "notifications@example.com"; may equal the sender.
EMAIL_TO = os.environ.get("VISA_EMAIL_TO", "")

# Inclusive random delay between completed observations; positive integer seconds.
# Examples: VISA_POLL_SECONDS_MIN="1800", VISA_POLL_SECONDS_MAX="3600" (30-60 minutes).
# Equal bounds give a fixed delay. These are local choices, NOT website-safe limits.
POLL_SECONDS_MIN = os.environ.get("VISA_POLL_SECONDS_MIN", 1800)
POLL_SECONDS_MAX = os.environ.get("VISA_POLL_SECONDS_MAX", 3600)

# These are conservative local budgets, NOT published website-safe limits.
BLOCK_COOLDOWN_SECONDS = 86400
MAX_RECOVERY_RETRIES = 3
MAX_CHECKS_PER_RUN = 24
AUTO_VERIFICATION = True
MAX_AUTO_VERIFICATIONS = 1
MANUAL_VERIFICATION_TIMEOUT_SECONDS = 900
PAGE_TIMEOUT_SECONDS = 45
QUEUE_TIMEOUT_SECONDS = 1800


def build_config():
    from visa_tool.core import Config
    from visa_tool.settings import (
        answer_setting, boolean_setting, integer_setting, range_setting, time_setting,
    )

    return Config(
        root=Path(__file__).resolve().parent,
        username=USERNAME,
        password=PASSWORD,
        security_answers=answer_setting("VISA_SECURITY_ANSWERS", SECURITY_ANSWERS),
        applicants=tuple(EXPECTED_APPLICANTS),
        visa_class=EXPECTED_VISA_CLASS,
        existing_appointment=EXPECTED_EXISTING_APPOINTMENT,
        ranges=range_setting("VISA_PREFERRED_DATE_RANGES", PREFERRED_DATE_RANGES),
        auto_booking=boolean_setting("VISA_AUTO_BOOKING", AUTO_BOOKING),
        daily_times=time_setting("VISA_DAILY_EMAIL_TIMES", DAILY_EMAIL_TIMES),
        smtp_host=SMTP_HOST,
        smtp_port=integer_setting("VISA_SMTP_PORT", SMTP_PORT),
        smtp_username=SMTP_USERNAME,
        smtp_password=SMTP_PASSWORD,
        email_to=EMAIL_TO,
        poll_seconds_min=integer_setting("VISA_POLL_SECONDS_MIN", POLL_SECONDS_MIN),
        poll_seconds_max=integer_setting("VISA_POLL_SECONDS_MAX", POLL_SECONDS_MAX),
        cooldown_seconds=BLOCK_COOLDOWN_SECONDS,
        max_retries=MAX_RECOVERY_RETRIES,
        max_checks=MAX_CHECKS_PER_RUN,
        auto_verification=AUTO_VERIFICATION,
        max_auto_verifications=MAX_AUTO_VERIFICATIONS,
        manual_timeout=MANUAL_VERIFICATION_TIMEOUT_SECONDS,
        page_timeout=PAGE_TIMEOUT_SECONDS,
        queue_timeout=QUEUE_TIMEOUT_SECONDS,
    )


if __name__ == "__main__":
    from visa_tool.app import cli

    raise SystemExit(cli(build_config))
