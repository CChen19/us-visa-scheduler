"""Configure here, then run: python main.py --check-config."""
import os
from pathlib import Path

# Use environment variables for secrets; never commit real passwords/answers.
USERNAME = os.environ.get("VISA_USERNAME", "")
PASSWORD = os.environ.get("VISA_PASSWORD", "")
SECURITY_ANSWERS = {
    # Exact displayed question text (Chinese or English): answer.
    # "Your configured question": os.environ.get("VISA_ANSWER_1", ""),
}
EXPECTED_APPLICANTS = [
    name.strip() for name in os.environ.get("VISA_APPLICANTS", "").split(";")
    if name.strip()
]
EXPECTED_VISA_CLASS = "B1/B2"  # Use the exact class displayed on your home page.
EXPECTED_EXISTING_APPOINTMENT = os.environ.get("VISA_EXISTING_APPOINTMENT", "")
# Above: Beijing local ISO datetime, e.g. "2027-02-03T09:00:00".
PREFERRED_DATE_RANGES = [
    ("2026-12-01", "2026-12-15"),
    ("2026-12-22", "2026-12-25"),
]
AUTO_BOOKING = False  # False means no date, time-slot, or Submit clicks.
DAILY_EMAIL_TIMES = ["08:00", "12:00", "17:00"]  # Beijing, UTC+08:00.

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465  # Implicit TLS; use an SMTP app password, not the visa password.
SMTP_USERNAME = os.environ.get("VISA_EMAIL_SENDER", "")
SMTP_PASSWORD = os.environ.get("VISA_SMTP_PASSWORD", "")
EMAIL_TO = os.environ.get("VISA_EMAIL_TO", "")

# These are conservative local budgets, NOT published website-safe limits.
POLL_SECONDS = 1800
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

    return Config(
        root=Path(__file__).resolve().parent,
        username=USERNAME,
        password=PASSWORD,
        security_answers=SECURITY_ANSWERS,
        applicants=tuple(EXPECTED_APPLICANTS),
        visa_class=EXPECTED_VISA_CLASS,
        existing_appointment=EXPECTED_EXISTING_APPOINTMENT,
        ranges=tuple(PREFERRED_DATE_RANGES),
        auto_booking=AUTO_BOOKING,
        daily_times=tuple(DAILY_EMAIL_TIMES),
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_username=SMTP_USERNAME,
        smtp_password=SMTP_PASSWORD,
        email_to=EMAIL_TO,
        poll_seconds=POLL_SECONDS,
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
