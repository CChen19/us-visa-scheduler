import unittest

from visa_tool.browser import reschedule_url, retry_seconds, safe_origin


class URLTests(unittest.TestCase):
    def test_reschedule_allowlist(self):
        self.assertTrue(reschedule_url("https://www.usvisascheduling.com/zh-CN/schedule/?reschedule=true"))
        for value in (
            "https://www.usvisascheduling.com/zh-CN/schedule/",
            "https://www.usvisascheduling.com/zh-CN/AppointmentManager/",
            "https://www.usvisascheduling.com.evil.invalid/zh-CN/schedule/?reschedule=true",
            "https://www.usvisascheduling.com/zh-CN/schedule/?reschedule=true&reschedule=false",
            "http://www.usvisascheduling.com/zh-CN/schedule/?reschedule=true",
        ):
            with self.subTest(value=value):
                self.assertFalse(reschedule_url(value))

    def test_credentials_origin_and_retry_after(self):
        self.assertTrue(safe_origin("https://atlasauth.b2clogin.com/path"))
        self.assertFalse(safe_origin("https://atlasauth.b2clogin.com.evil.invalid/path"))
        self.assertEqual(retry_seconds("120"), 120)
        self.assertEqual(retry_seconds("invalid"), 0)


if __name__ == "__main__":
    unittest.main()
