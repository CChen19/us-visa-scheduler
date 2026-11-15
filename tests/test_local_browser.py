"""Opt-in real Chrome tests. All pages/requests are synthetic and loopback-only."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from test_core import configuration
from visa_tool.browser import POST_ID, SiteBrowser
from visa_tool.core import AccessBlocked, SafetyError


PAGE = r"""<!doctype html><meta charset="utf-8"><title>VisaTool LOCAL fixture</title>
<body>
<div id="home"><span>Visa Information</span><p>WUHAN<br>B1/B2</p>
<a id="reschedule_appointment" href="/zh-CN/schedule/?reschedule=true">Reschedule</a>
<button id="cancel" onclick="window.cancelClicked=true">Never click cancel</button></div>
<div id="schedule">
<ul id="gm_select"><li><label>Test Applicant</label></li></ul>
<select id="post_select" onchange="postChange()"><option value=""></option>
<option value="POST_ID">WUHAN</option></select>
<div id="datepicker">
<select class="ui-datepicker-year" onchange="renderDays()">
<option value="2026">2026</option><option value="2027" selected>2027</option></select>
<select class="ui-datepicker-month" onchange="renderDays()">
<option value="0">January</option><option value="6" selected>July</option>
<option value="7">August</option><option value="11">December</option></select>
<table><tbody id="days"></tbody></table></div>
<div id="datepicker-message"></div><table id="time_select"></table>
<input id="submitbtn" type="submit" disabled value="Submit" onclick="submitBooking()">
</div>
<script>
var consularAppointments=[{appointmentDateStr:"2027/2/3 9:00:00",windowsTimeZone:"China Standard Time"}];
var applications=["synthetic-application"], jsdata={}, chosen=null;
var callbacks={}, returnedDays=[];
function jQuery(target) { return {
 on:function(event,handler){ callbacks[event.split(".")[0]]=handler; },
 val:function(){return document.querySelector(target).value;},
 datepicker:function(method){ if(method==="getDate") return chosen; }
}; }
function request(route, data, complete) {
 let xhr=new XMLHttpRequest(), settings={url:"/custom-actions/?route=/api/v1/schedule-group/"+route,
 data:"parameters="+JSON.stringify(data)};
 xhr.open("POST",settings.url);
 if(callbacks.ajaxSend) callbacks.ajaxSend(null,xhr,settings);
 xhr.onload=function(){
  try {xhr.responseJSON=JSON.parse(xhr.responseText);} catch(_) {}
  complete(xhr.responseJSON);
  if(callbacks.ajaxComplete) callbacks.ajaxComplete(null,xhr,settings);
 };
 xhr.send(settings.data);
}
function postChange(){
 document.querySelector("#time_select").innerHTML="";
 jsdata={postId:document.querySelector("#post_select").value};
 if(!jsdata.postId) return;
 request("get-family-consular-schedule-days",jsdata,function(data){
  if(data && data.ScheduleDays){returnedDays=data.ScheduleDays; renderDays();}
 });
}
function renderDays(){
 let year=Number(document.querySelector(".ui-datepicker-year").value);
 let month=Number(document.querySelector(".ui-datepicker-month").value);
 document.querySelector("#days").innerHTML=returnedDays.filter(d=>{
  let x=new Date(d.Date); return x.getUTCFullYear()===year && x.getUTCMonth()===month;
 }).map(d=>{
  let day=Number(d.Date.slice(8,10));
  return `<tr><td class="greenday" data-handler="selectDay" data-year="${year}" data-month="${month}">
  <a href="#" data-date="${day}" onclick="chooseDay('${d.Date}');return false">${day}</a></td></tr>`;
 }).join("");
}
function chooseDay(day){
 chosen=new Date(Number(day.slice(0,4)),Number(day.slice(5,7))-1,Number(day.slice(8,10)));
 jsdata.Date=day+"T00:00:00.000Z";
 request("get-family-consular-schedule-entries",jsdata,function(data){
  document.querySelector("#time_select").innerHTML=data.ScheduleEntries.map((e,i)=>
  `<tr><td><label><input type="radio" name="schedule-entries" value="${i+1}" data-slots="${e.EntriesAvailable}"
  onclick="jsdata.Num=this.value; document.querySelector('#submitbtn').disabled=false">${day.replaceAll("-","/")}</label></td>
  <td>${e.Time}</td><td>${e.EntriesAvailable}</td></tr>`).join("");
 });
}
function submitBooking(){
 document.querySelector("#submitbtn").disabled=true;
 request("reschedule-consular-appointments-for-family",jsdata,function(data){
  if(data.AllScheduled) location.href="/zh-CN/appointment-confirmation/";
 });
}
if(location.pathname==="/zh-CN/"){document.querySelector("#schedule").remove();}
else {document.querySelector("#home").remove();}
</script></body>""".replace("POST_ID", POST_ID)

AUTH = """<!doctype html><title>Local authentication fixture</title>
<input id="signInName"><input id="password" type="password">
<button id="continue" onclick="document.body.innerHTML=questions">Continue</button>
<script>
let questions = `<p id="kbq1ReadOnly">Question One?</p><input id="kba1_response" type="password">
<p id="kbq3ReadOnly">Question Three?</p><input id="kba3_response" type="password">
<button id="continue" onclick="window.answers=[
document.querySelector('#kba1_response').value,document.querySelector('#kba3_response').value]">Continue</button>`;
</script>"""


class FixtureHandler(BaseHTTPRequestHandler):
    days = ["2026-12-01", "2026-12-16", "2026-12-23", "2027-08-03"]
    blocked = False
    submits = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/auth":
            body = AUTH
        elif path == "/zh-CN/appointment-confirmation/":
            body = "<title>Local confirmation</title><p>Fixture confirmation</p>"
        else:
            body = PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        route = parse_qs(urlparse(self.path).query)["route"][0]
        status = 200
        if route.endswith("schedule-days"):
            if self.blocked:
                status, result = 429, {}
            else:
                result = {"ScheduleDays": [{"Date": day} for day in self.days], "Token": "synthetic"}
        elif route.endswith("schedule-entries"):
            result = {"ScheduleEntries": [
                {"Time": "08:00", "EntriesAvailable": 0},
                {"Time": "08:30", "EntriesAvailable": 7},
            ], "Token": "synthetic"}
        else:
            type(self).submits += 1
            result = {"AllScheduled": True, "HasError": False}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if status == 429:
            self.send_header("Retry-After", "123")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


@unittest.skipUnless(os.environ.get("VISA_LOCAL_BROWSER_TESTS") == "1", "Opt-in loopback Chrome tests")
class LocalBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from seleniumbase import Driver
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.driver = Driver(headless=True, page_load_strategy="eager")
        cls.driver.execute_cdp_cmd("Network.enable", {})
        cls.driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": [
            "*usvisascheduling.com*", "*b2clogin.com*", "*cloudflare.com*",
        ]})

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.config = configuration(Path(self.temp.name), auto_booking=True, page_timeout=5)
        self.browser = SiteBrowser(self.driver, self.config)
        self.origin_patch = patch("visa_tool.browser.safe_origin",
                                  side_effect=lambda url: url.startswith(self.base + "/"))
        self.reschedule_patch = patch(
            "visa_tool.browser.reschedule_url",
            side_effect=lambda url: url.startswith(self.base + "/zh-CN/schedule/?reschedule=true"),
        )
        self.auth_patch = patch("visa_tool.browser.AUTH_HOST", urlparse(self.base).netloc)
        self.origin_patch.start()
        self.reschedule_patch.start()
        self.auth_patch.start()
        FixtureHandler.blocked = False
        FixtureHandler.days = ["2026-12-01", "2026-12-16", "2026-12-23", "2027-08-03"]
        FixtureHandler.submits = 0

    def tearDown(self):
        self.origin_patch.stop()
        self.reschedule_patch.stop()
        self.auth_patch.stop()
        self.temp.cleanup()

    def enter(self):
        self.driver.get(self.base + "/zh-CN/")
        self.browser.enter_reschedule()
        self.browser._wait(lambda: self.browser.visible("#post_select"), "Fixture did not load.")

    def test_calendar_all_months_slot_time_and_confirmed_response(self):
        self.enter()
        dates = self.browser.load_dates()
        self.assertIn("2026-12-23", dates)  # Calendar initially displays July 2027.
        with self.assertRaises(SafetyError):
            self.browser.prepare_slot("2026-12-16")
        slot = self.browser.prepare_slot("2026-12-23")
        self.assertEqual(slot.time, "08:30")
        self.assertEqual(slot.entry, "2")  # First row has no capacity.
        self.browser.submit(slot)
        result = self.browser._wait(lambda: self.browser.outcome(slot) == "success", "Missing outcome.")
        self.assertTrue(result)
        self.assertEqual(FixtureHandler.submits, 1)

    def test_empty_days_and_rate_limit_are_distinct(self):
        self.enter()
        FixtureHandler.days = []
        self.assertEqual(self.browser.load_dates(), [])
        FixtureHandler.blocked = True
        with self.assertRaises(AccessBlocked) as caught:
            self.browser.load_dates()
        self.assertEqual(caught.exception.retry_after, 123)

    def test_real_dom_question_1_and_3_mapping(self):
        self.driver.get(self.base + "/auth")
        self.browser.login()
        self.browser._wait(lambda: self.browser.visible("#kbq3ReadOnly"), "Questions did not appear.")
        self.browser.answer_questions()
        self.assertEqual(self.driver.execute_script("return window.answers"), ["one", "three"])

    def test_monitor_only_adapter_rejects_date_click(self):
        self.enter()
        self.browser.load_dates()
        self.browser.config.auto_booking = False
        with self.assertRaises(SafetyError):
            self.browser.prepare_slot("2026-12-23")
        self.assertIsNone(self.driver.execute_script("return chosen"))
        self.assertEqual(FixtureHandler.submits, 0)

    def test_stale_selection_and_changed_existing_appointment_block_submit(self):
        self.enter()
        self.browser.load_dates()
        slot = self.browser.prepare_slot("2026-12-23")
        self.driver.execute_script("jsdata.Date = '2026-12-16T00:00:00Z';")
        with self.assertRaises(SafetyError):
            self.browser.submit(slot)
        self.assertEqual(FixtureHandler.submits, 0)
        self.driver.execute_script("consularAppointments[0].appointmentDateStr='2027/2/4 9:00:00';")
        with self.assertRaises(SafetyError):
            self.browser.load_dates()

    def test_all_question_pairs_and_unknown_question(self):
        self.driver.get(self.base + "/auth")
        questions = {1: "Question One?", 2: "Question Two?", 3: "Question Three?"}
        answers = {1: "one", 2: "two", 3: "three"}
        for pair in ((1, 2), (1, 3), (2, 3)):
            body = "".join(
                f'<p id="kbq{index}ReadOnly">{questions[index]}</p>'
                f'<input id="kba{index}_response" type="password">' for index in pair
            )
            body += '<button id="continue" onclick="window.clicked=true">Continue</button>'
            self.driver.execute_script("document.body.innerHTML=arguments[0]; window.clicked=false;", body)
            self.browser.answer_questions()
            for index in pair:
                self.assertEqual(self.driver.find_element("id", f"kba{index}_response").get_attribute("value"),
                                 answers[index])
            self.assertTrue(self.driver.execute_script("return window.clicked"))
        self.driver.execute_script("document.querySelector('#kbq2ReadOnly').textContent='Unknown question';")
        with self.assertRaises(SafetyError):
            self.browser.answer_questions()


@unittest.skipUnless(os.environ.get("VISA_LOCAL_BROWSER_TESTS") == "1" and os.name == "nt",
                     "Opt-in isolated UC process smoke test on Windows")
class WorkerSmokeTest(unittest.TestCase):
    def test_uc_worker_starts_blank_and_owned_job_stops_it(self):
        from visa_tool.browser import BrowserPort
        from visa_tool.core import Clock, Logs, Mailbox, Runtime, Store
        with TemporaryDirectory() as directory:
            config = configuration(directory)
            clock = Clock()
            logs = Logs(config.root, clock)
            store = Store(config.root)
            mail = Mailbox(config, store, logs, clock, lambda *_: None)
            runtime = Runtime(config, store, logs, mail, clock)
            runtime.notifications_enabled = False
            port = None
            try:
                port = BrowserPort(config, runtime)
                self.assertTrue(port.process.is_alive())
                # Never call open(): production open() targets the real sign-in URL.
                port.close(force=True)
                self.assertFalse(port.process.is_alive())
            finally:
                if port is not None:
                    port.close(force=True)
                logs.close()


if __name__ == "__main__":
    unittest.main()
