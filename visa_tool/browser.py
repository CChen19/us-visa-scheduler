"""Browser-only worker. Never persists credentials, page sources, or tokens."""
from __future__ import annotations

import ctypes
import multiprocessing
import os
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from .core import (
    POST_ID, AccessBlocked, SafetyError, Slot, WorkerLost, normalized, parse_day,
    parse_local_datetime,
)

SITE = "https://www.usvisascheduling.com"
SIGN_IN = SITE + "/zh-CN/SignIn"
AUTH_HOST = "atlasauth.b2clogin.com"
DAY_ROUTE = "get-family-consular-schedule-days"
ENTRY_ROUTE = "get-family-consular-schedule-entries"
SUBMIT_ROUTE = "reschedule-consular-appointments-for-family"

# Observe only normal scheduling responses. No direct API calls or token capture.
OBSERVER = r"""
if (!window.jQuery) return false;
if (window.__visaWatch) return true;
window.__visaWatch = {days:null, entries:null, outcome:null, blocked:null, error:null};
jQuery(document).on("ajaxSend.visaWatch", function(_, xhr, settings) {
    let route = new URL(settings.url, location.href).searchParams.get("route") || "";
    if (!route.endsWith("reschedule-consular-appointments-for-family")) return;
    try {
        let body = JSON.parse(new URLSearchParams(settings.data).get("parameters"));
        xhr.__visaRequest = {day:String(body.Date || "").slice(0,10),
                             entry:String(body.Num || "")};
    } catch (_) { xhr.__visaRequest = null; }
});
jQuery(document).on("ajaxComplete.visaWatch", function(_, xhr, settings) {
    let route = new URL(settings.url, location.href).searchParams.get("route") || "";
    let kind = route.endsWith("get-family-consular-schedule-days") ? "days" :
               route.endsWith("get-family-consular-schedule-entries") ? "entries" :
               route.endsWith("reschedule-consular-appointments-for-family") ? "outcome" : null;
    if (!kind) return;
    let state = window.__visaWatch;
    if (xhr.status === 429 || (xhr.status === 403 &&
        xhr.getResponseHeader("cf-mitigated") === "challenge")) {
        state.blocked = {status:xhr.status, retry_after:xhr.getResponseHeader("Retry-After")};
    }
    let data = xhr.responseJSON;
    if (!data) { try { data = JSON.parse(xhr.responseText); } catch (_) {} }
    if (!data || xhr.status < 200 || xhr.status >= 300) {
        state.error = "SCHEDULING_HTTP_ERROR";
        return;
    }
    if (kind === "outcome") {
        state.outcome = {has_error:!!data.HasError, all_scheduled:!!data.AllScheduled,
                         request:xhr.__visaRequest};
        // The success callback can navigate away before Python observes it.
        sessionStorage.setItem("visaWatchOutcome", JSON.stringify(state.outcome));
    } else if (data.HasError) {
        state.error = "SCHEDULING_APPLICATION_ERROR";
    } else if (kind === "days") {
        state.days = Array.isArray(data.ScheduleDays) ?
            data.ScheduleDays.map(d => {
                let date = new Date(d.Date);
                if (isNaN(date.getTime())) return null;
                return [date.getFullYear(), String(date.getMonth()+1).padStart(2,"0"),
                        String(date.getDate()).padStart(2,"0")].join("-");
            }) : "invalid";
    } else {
        state.entries = Array.isArray(data.ScheduleEntries) ?
            data.ScheduleEntries.length : "invalid";
    }
});
return true;
"""


def reschedule_url(url):
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.netloc == "www.usvisascheduling.com"
            and parsed.path.rstrip("/") == "/zh-CN/schedule"
            and parse_qs(parsed.query).get("reschedule") == ["true"])


def safe_origin(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc in (
        "www.usvisascheduling.com", AUTH_HOST,
    )


def retry_seconds(value):
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        from email.utils import parsedate_to_datetime
        from datetime import timezone
        try:
            return max(0, (parsedate_to_datetime(value)
                           - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class SiteBrowser:
    def __init__(self, driver, config):
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import Select, WebDriverWait
        self.driver, self.config = driver, config
        self.By, self.Select, self.Wait = By, Select, WebDriverWait
        self.family_ids = None
        driver.set_page_load_timeout(config.page_timeout)
        driver.set_script_timeout(15)
        self._timezone()

    def _timezone(self):
        self.driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Shanghai"})

    def elements(self, selector):
        return self.driver.find_elements(self.By.CSS_SELECTOR, selector)

    def visible(self, selector):
        return [element for element in self.elements(selector) if element.is_displayed()]

    def _wait(self, predicate, message):
        from selenium.common.exceptions import TimeoutException
        try:
            return self.Wait(self.driver, self.config.page_timeout, poll_frequency=0.5).until(
                lambda _: predicate())
        except TimeoutException as exc:
            raise SafetyError(message) from exc

    def open(self):
        self.driver.get(SIGN_IN)
        self._timezone()

    def inspect(self):
        if not safe_origin(self.driver.current_url):
            raise SafetyError("Unexpected browser origin. No action permitted.")
        body = self.driver.find_element(self.By.TAG_NAME, "body").text
        lower = body.lower()
        observed = self.driver.execute_script("return window.__visaWatch || null;")
        if observed and observed.get("blocked"):
            block = observed["blocked"]
            raise AccessBlocked("Scheduling request restricted.", retry_seconds(block.get("retry_after")))
        blocked_markers = (
            "too many requests", "you are being rate limited", "access limitation",
            "temporarily blocked", "account suspended", "sorry, you have been blocked",
            "exceeded the limit", "approaching the maximum",
            "您查看本页面的次数已接近上限", "您已超过浏览此页面的次数上限",
            "访问限制", "暂时禁止", "请求过多",
        )
        if any(marker in lower for marker in blocked_markers):
            raise AccessBlocked("Access or page-view restriction detected.")
        if any(marker in lower for marker in ("已达到预约次数上限", "pse0602", "pse0610", "pse0612")):
            raise SafetyError("The site reports an appointment/application restriction; no automatic recovery.")
        if "you are now in line" in lower or "virtual queue" in lower:
            return {"state": "queue"}
        challenges = self.visible(
            "#challenge-stage, #challenge-running, #challenge-form, "
            "iframe[src*='challenges.cloudflare.com'], input[name='cf-turnstile-response']"
        )
        if challenges or any(marker in lower for marker in (
            "verifying you are human", "verify you are human", "performing security verification",
            "验证您是真人", "正在验证您是否", "安全验证",
        )):
            return {"state": "challenge"}
        errors = self.visible("#claimVerificationServerError")
        if any(element.text.strip() for element in errors):
            raise SafetyError("Authentication rejected; no automatic credential retry.")
        if self.visible("#signInName") and self.visible("#password"):
            return {"state": "login"}
        questions = self.visible("[id^='kbq'][id$='ReadOnly']")
        if questions:
            return {"state": "questions", "questions": [
                {"id": item.get_attribute("id"), "text": item.text} for item in questions
            ]}
        if self.visible("#reschedule_appointment"):
            return {"state": "home"}
        if reschedule_url(self.driver.current_url) and self.visible("#post_select"):
            return {"state": "schedule"}
        return {"state": "unknown"}

    def login(self):
        if self.inspect()["state"] != "login" or urlparse(self.driver.current_url).netloc != AUTH_HOST:
            raise SafetyError("Login form or authentication origin changed.")
        for selector, value in (("#signInName", self.config.username), ("#password", self.config.password)):
            element = self.visible(selector)[0]
            element.clear()
            element.send_keys(value)
        self.visible("#continue")[0].click()

    def answer_questions(self):
        info = self.inspect()
        if info["state"] != "questions" or urlparse(self.driver.current_url).netloc != AUTH_HOST:
            raise SafetyError("Security-question form or authentication origin changed.")
        answers = {normalized(key): value for key, value in self.config.security_answers.items()}
        planned = []
        seen = set()
        for question in info["questions"]:
            match = re.fullmatch(r"kbq([123])ReadOnly", question["id"])
            answer = answers.get(normalized(question["text"]))
            if not match or not answer or question["id"] in seen:
                raise SafetyError("Unknown security question; configure its exact text locally.")
            seen.add(question["id"])
            targets = self.visible(f"#kba{match.group(1)}_response")
            if len(targets) != 1:
                raise SafetyError("Ambiguous security answer field.")
            planned.append((targets[0], answer))
        if len(planned) != 2:
            raise SafetyError("Expected exactly two security questions.")
        for target, answer in planned:
            target.clear()
            target.send_keys(answer)
        self.visible("#continue")[0].click()

    def gui_verify(self):
        parsed = urlparse(self.driver.current_url)
        if self.inspect()["state"] != "challenge" or not (
            parsed.netloc == AUTH_HOST or parsed.path.lower().rstrip("/") == "/zh-cn/signin"
        ):
            return {"attempted": False, "reason": "GUI helper is limited to authentication pages."}
        if os.name != "nt":
            return {"attempted": False, "reason": "This GUI adapter requires an interactive Windows desktop."}
        user32 = ctypes.windll.user32
        window = user32.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(window, title, len(title))
        if not window or user32.IsIconic(window) or not self.driver.title or self.driver.title not in title.value:
            return {"attempted": False, "reason": "Browser is not safely foregrounded."}
        self.driver.uc_gui_click_captcha(retry=False, blind=False)
        return {"attempted": True}

    def enter_reschedule(self):
        if self.inspect()["state"] != "home":
            raise SafetyError("Expected authenticated home page.")
        cards = self.driver.find_elements(
            self.By.XPATH, "//span[normalize-space()='Visa Information']/following-sibling::p"
        )
        if len(cards) != 1:
            raise SafetyError("Cannot verify home-page visa information.")
        lines = [normalized(line) for line in cards[0].text.splitlines()]
        if self.config.visa_class not in lines or not any("WUHAN" in line.upper() for line in lines):
            raise SafetyError("Home-page visa class/post does not match the configured Wuhan application.")
        link = self.visible("#reschedule_appointment")[0]
        if not reschedule_url(link.get_attribute("href")):
            raise SafetyError("Home-page reschedule link is unexpected.")
        self._timezone()
        link.click()

    def _context(self):
        state = self.inspect()["state"]
        if state != "schedule":
            raise SafetyError("Rescheduling page is not ready; reauthentication may be required.")
        if self.driver.execute_script("return Intl.DateTimeFormat().resolvedOptions().timeZone;") != "Asia/Shanghai":
            raise SafetyError("Browser timezone changed; refusing potentially shifted appointment dates.")
        applicants = sorted(normalized(item.text) for item in self.visible("#gm_select label"))
        if applicants != sorted(map(normalized, self.config.applicants)):
            raise SafetyError("Applicant group does not match configuration.")
        data = self.driver.execute_script("""
            return {appointments: typeof consularAppointments === "undefined" ? [] : consularAppointments,
                    family: typeof applications === "undefined" ? [] : applications};
        """)
        if not data["family"] or len(data["family"]) != len(applicants):
            raise SafetyError("Cannot verify the active application group.")
        if self.family_ids is not None and data["family"] != self.family_ids:
            raise SafetyError("Application group changed during monitoring.")
        self.family_ids = data["family"]
        expected = parse_local_datetime(self.config.existing_appointment)
        appointments = data["appointments"]
        if len(appointments) != 1:
            raise SafetyError("Expected one existing consular appointment.")
        item = appointments[0]
        if item.get("windowsTimeZone") != "China Standard Time":
            raise SafetyError("Unexpected appointment timezone.")
        text = item.get("appointmentDateStr", "")
        try:
            old = datetime.strptime(text, "%Y/%m/%d %H:%M:%S")
        except ValueError as exc:
            raise SafetyError("Unrecognized existing appointment timestamp.") from exc
        if old != expected:
            raise SafetyError("Existing appointment differs from configuration; reconcile before continuing.")

    def load_dates(self):
        self._wait(lambda: self.visible("#gm_select label"), "Applicant list did not load.")
        self._context()
        if not self.driver.execute_script(OBSERVER):
            raise SafetyError("Normal scheduling response observer could not be installed.")
        self.driver.execute_script("""
            window.__visaWatch.days = null; window.__visaWatch.entries = null;
            window.__visaWatch.error = null; window.__visaWatch.blocked = null;
        """)
        select = self.Select(self.visible("#post_select")[0])
        self._wait(lambda: any(option.get_attribute("value") == POST_ID for option in select.options),
                   "Wuhan is not offered for this application.")
        # Reselect through the normal UI, without refreshing the tracked page.
        select.select_by_value("")
        select.select_by_value(POST_ID)

        def loaded():
            self.inspect()
            data = self.driver.execute_script("return window.__visaWatch;")
            if data.get("error"):
                raise SafetyError("Availability request failed; no empty snapshot will be invented.")
            return data if data["days"] is not None else False

        result = self._wait(loaded, "Availability response timed out.")
        if not isinstance(result["days"], list):
            raise SafetyError("Unrecognized schedule-day response.")
        return sorted({parse_day(value).isoformat() for value in result["days"]})

    def prepare_slot(self, day):
        if not self.config.auto_booking or not self.config.priority(parse_day(day)):
            raise SafetyError("Date selection is disabled or outside preferred ranges.")
        self._context()
        if self.Select(self.visible("#post_select")[0]).first_selected_option.get_attribute("value") != POST_ID:
            raise SafetyError("Selected post changed.")
        requested = parse_day(day)
        years = self.visible("#datepicker .ui-datepicker-year")
        months = self.visible("#datepicker .ui-datepicker-month")
        if not years or not months or years[0].tag_name != "select" or months[0].tag_name != "select":
            raise SafetyError("Calendar month/year navigation is unavailable.")
        self.Select(years[0]).select_by_value(str(requested.year))
        self.Select(self.visible("#datepicker select.ui-datepicker-month")[0]).select_by_value(
            str(requested.month - 1))
        locator = (
            f"#datepicker td.greenday[data-handler='selectDay'][data-year='{requested.year}']"
            f"[data-month='{requested.month - 1}'] a[data-date='{requested.day}']"
        )
        targets = self.visible(locator)
        if len(targets) != 1:
            return None
        self.driver.execute_script("window.__visaWatch.entries = null; window.__visaWatch.error = null;")
        targets[0].click()

        def entries_loaded():
            self.inspect()
            observation = self.driver.execute_script("return window.__visaWatch;")
            if observation["error"]:
                raise SafetyError("Time-slot request failed.")
            return observation if observation["entries"] is not None else False

        observation = self._wait(entries_loaded, "Time-slot response timed out.")
        if not isinstance(observation["entries"], int):
            raise SafetyError("Unrecognized time-slot response.")
        if observation["entries"] == 0:
            return None
        for radio in self.visible("#time_select input[name='schedule-entries']"):
            row = radio.find_element(self.By.XPATH, "./ancestor::tr")
            cells = row.find_elements(self.By.TAG_NAME, "td")
            if len(cells) != 3 or parse_day(cells[0].text).isoformat() != day:
                raise SafetyError("Time-slot table does not match the selected date.")
            time_text = cells[1].text.strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_text):
                raise SafetyError("Unrecognized time-slot time.")
            try:
                capacity = int(radio.get_attribute("data-slots"))
            except (TypeError, ValueError) as exc:
                raise SafetyError("Unrecognized slot capacity.") from exc
            if radio.is_enabled() and capacity >= len(self.config.applicants):
                radio.click()
                slot = Slot(day, time_text, radio.get_attribute("value"), capacity)
                self.preflight(slot)
                return slot
        return None

    def preflight(self, slot):
        if not self.config.auto_booking or not self.config.priority(parse_day(slot.day)):
            raise SafetyError("Submission is disabled or date is not preferred.")
        self._context()
        data = self.driver.execute_script("""
            let d = jQuery("#datepicker").datepicker("getDate");
            let day = d ? [d.getFullYear(), String(d.getMonth()+1).padStart(2,"0"),
                           String(d.getDate()).padStart(2,"0")].join("-") : null;
            return {day:day, entry:String(jsdata.Num || ""), post:jQuery("#post_select").val(),
                    requestDay: jsdata.Date ? new Date(jsdata.Date).toISOString().slice(0,10) : null};
        """)
        if data != {"day": slot.day, "entry": slot.entry, "post": POST_ID, "requestDay": slot.day}:
            raise SafetyError("Selected date/entry/post differs from the planned submission.")
        selected = [item for item in self.visible("#time_select input[name='schedule-entries']")
                    if item.is_selected()]
        if len(selected) != 1 or selected[0].get_attribute("value") != slot.entry:
            raise SafetyError("No unique matching time-slot selection.")
        cells = selected[0].find_element(self.By.XPATH, "./ancestor::tr").find_elements(self.By.TAG_NAME, "td")
        if (len(cells) != 3 or parse_day(cells[0].text).isoformat() != slot.day
                or cells[1].text.strip() != slot.time
                or int(selected[0].get_attribute("data-slots")) < len(self.config.applicants)):
            raise SafetyError("Selected time-slot details changed.")
        buttons = self.visible("#submitbtn")
        if len(buttons) != 1 or not buttons[0].is_enabled():
            raise SafetyError("Submit is not uniquely available.")
        return True

    def submit(self, slot):
        self.preflight(slot)
        self.driver.execute_script("""
            sessionStorage.removeItem("visaWatchOutcome");
            window.__visaWatch.outcome = null;
        """)
        self.visible("#submitbtn")[0].click()

    def outcome(self, slot):
        if not safe_origin(self.driver.current_url):
            return "unknown"
        result = self.driver.execute_script("""
            let current = window.__visaWatch && window.__visaWatch.outcome;
            if (current) return current;
            try { return JSON.parse(sessionStorage.getItem("visaWatchOutcome")); }
            catch (_) { return null; }
        """)
        if not result:
            return "unknown"
        if result.get("has_error"):
            return "failure"
        request = result.get("request")
        if result.get("all_scheduled") and request == {"day": slot.day, "entry": slot.entry}:
            return "success"
        return "unknown"


def _worker(connection, config, ready):
    driver = None
    try:
        ready.wait()
        from seleniumbase import Driver
        driver = Driver(uc=True, headless=False, page_load_strategy="eager")
        browser = SiteBrowser(driver, config)
        connection.send(("ok", None))
        while True:
            method, args = connection.recv()
            if method == "close":
                break
            try:
                value = getattr(browser, method)(*args)
            except AccessBlocked as exc:
                connection.send(("blocked", (str(exc), exc.retry_after)))
            except SafetyError as exc:
                connection.send(("error", str(exc)))
            except Exception as exc:
                # External exceptions often contain URLs, form values or tokens.
                connection.send(("error", f"Browser operation failed ({type(exc).__name__})."))
            else:
                connection.send(("ok", value))
    except (EOFError, BrokenPipeError):
        pass  # The owning process has closed this worker's transport.
    except Exception as exc:
        connection.send(("error", f"Browser startup/worker failure ({type(exc).__name__})."))
    finally:
        if driver is not None:
            driver.quit()
        connection.close()


class WindowsJob:
    """Keep the owned worker and Chrome descendants inside one kill-on-close job."""
    def __init__(self, pid):
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise SafetyError("Cannot create isolated browser process job.")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process_handle = None
        try:
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise SafetyError("Cannot configure browser process isolation.")
            process_handle = kernel.OpenProcess(0x0101, False, pid)
            if not process_handle or not kernel.AssignProcessToJobObject(self.handle, process_handle):
                raise SafetyError("Cannot isolate browser worker; no browser will be started.")
        except BaseException:
            self.close()
            raise
        finally:
            if process_handle:
                kernel.CloseHandle(process_handle)

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise SafetyError("Failed to close the owned browser job.")
            self.handle = None


class BrowserPort:
    def __init__(self, config, runtime):
        self.config, self.runtime = config, runtime
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        ready = context.Event()
        self.job = None
        self.process = context.Process(target=_worker, args=(child, config, ready), daemon=True)
        self.process.start()
        child.close()
        try:
            if os.name == "nt":
                self.job = WindowsJob(self.process.pid)
            ready.set()
            self._receive(120)
        except BaseException:
            self.close()
            raise

    def _receive(self, timeout):
        deadline = self.runtime.clock.monotonic() + timeout
        while self.runtime.clock.monotonic() < deadline:
            if self.connection.poll(0.1):
                try:
                    kind, value = self.connection.recv()
                except EOFError as exc:
                    raise WorkerLost("Browser worker stopped unexpectedly.") from exc
                if kind == "blocked":
                    raise AccessBlocked(*value)
                if kind == "error":
                    raise SafetyError(value)
                return value
            if not self.process.is_alive():
                raise WorkerLost("Browser worker exited unexpectedly.")
            self.runtime.tick()
        self.close(force=True)
        raise WorkerLost("Browser operation timed out; its process tree was stopped.")

    def call(self, method, *args, timeout=None):
        self.runtime.logs.event(method.upper(), "started")
        try:
            self.connection.send((method, args))
            result = self._receive(timeout or self.config.page_timeout + 20)
        except (EOFError, BrokenPipeError, OSError) as exc:
            self.runtime.logs.event(method.upper(), "fail", errorDetails="Browser transport failed.")
            raise WorkerLost("Browser transport failed.") from exc
        except SafetyError as exc:
            self.runtime.logs.event(method.upper(), "fail", errorDetails=str(exc))
            raise
        self.runtime.logs.event(method.upper())
        return result

    def close(self, force=False):
        if not self.process.is_alive():
            if self.job is not None:
                self.job.close()
            self.process.join(timeout=1)
            self.connection.close()
            return
        if not force:
            try:
                self.connection.send(("close", ()))
            except (BrokenPipeError, EOFError, OSError):
                force = True
            if not force:
                self.process.join(timeout=10)
        if self.process.is_alive():
            if self.job is not None:
                self.job.close()
            else:
                self.process.terminate()
            self.process.join(timeout=5)
        if self.process.is_alive():
            raise SafetyError("Owned browser worker did not stop.")
        if self.job is not None:
            self.job.close()
        self.connection.close()
