"""
Multi-User Session Isolation Tests
Ensures zero data leakage between different students, valid tokenized browser sessions,
and strict rejection of unauthenticated requests.
"""

import unittest
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.web_server import app, ACTIVE_SESSIONS, save_sessions
from src.schedule_parser import CalendarEvent
from src.schedule_cache import save_feed_cache, load_feed_cache, save_cached_exams, load_cached_exams


class TestMultiUserSessions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import src.web_server as ws
        cls._orig_sessions_file = ws.SESSIONS_FILE
        cls._test_sessions_file = os.path.join(PROJECT_ROOT, "data", ".test_sessions.json")
        ws.SESSIONS_FILE = cls._test_sessions_file

    @classmethod
    def tearDownClass(cls):
        import src.web_server as ws
        ws.SESSIONS_FILE = cls._orig_sessions_file
        if os.path.exists(cls._test_sessions_file):
            try:
                os.remove(cls._test_sessions_file)
            except Exception:
                pass

    def setUp(self):
        self.client = app.test_client()
        # Clean session store in isolated test file
        ACTIVE_SESSIONS.clear()
        save_sessions()

    def test_unauthenticated_status_returns_false(self):
        """Unauthenticated browser visiting /api/status MUST receive authenticated: False."""
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get("authenticated"))
        self.assertNotIn("profile", data)

    def test_overview_unauthenticated_returns_401(self):
        """Unauthenticated request to /api/overview MUST be rejected with 401."""
        res = self.client.get("/api/overview")
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertFalse(data.get("success"))

    def test_considerations_unauthenticated_returns_401(self):
        """Unauthenticated request to /api/considerations MUST be rejected with 401."""
        res = self.client.get("/api/considerations")
        self.assertEqual(res.status_code, 401)
        data = res.get_json()
        self.assertFalse(data.get("success"))

    def test_session_isolation_between_two_students(self):
        """Simulate two students logging in simultaneously with distinct sessions."""
        # Inject Student A session
        token_a = "session_token_student_A_12345"
        ACTIVE_SESSIONS[token_a] = {
            "session_token": token_a,
            "mssv": "20110001",
            "name": "Nguyễn Văn A",
            "role": "SV",
            "status": "Còn học",
            "portal_token": "mock_jwt_portal_token_A",
            "session_data": {"Id": "20110001", "FullName": "Nguyễn Văn A"},
        }

        # Inject Student B session
        token_b = "session_token_student_B_67890"
        ACTIVE_SESSIONS[token_b] = {
            "session_token": token_b,
            "mssv": "20110002",
            "name": "Trần Văn B",
            "role": "SV",
            "status": "Còn học",
            "portal_token": "mock_jwt_portal_token_B",
            "session_data": {"Id": "20110002", "FullName": "Trần Văn B"},
        }
        save_sessions()

        # Student A queries /api/status with their token
        res_a = self.client.get("/api/status", headers={"X-Session-Token": token_a})
        self.assertEqual(res_a.status_code, 200)
        data_a = res_a.get_json()
        self.assertTrue(data_a.get("authenticated"))
        self.assertEqual(data_a["profile"]["id"], "20110001")
        self.assertEqual(data_a["profile"]["name"], "Nguyễn Văn A")

        # Student B queries /api/status with their token
        res_b = self.client.get("/api/status", headers={"X-Session-Token": token_b})
        self.assertEqual(res_b.status_code, 200)
        data_b = res_b.get_json()
        self.assertTrue(data_b.get("authenticated"))
        self.assertEqual(data_b["profile"]["id"], "20110002")
        self.assertEqual(data_b["profile"]["name"], "Trần Văn B")

        # An unauthenticated visitor queries /api/status (no token)
        res_stranger = self.client.get("/api/status")
        self.assertEqual(res_stranger.status_code, 200)
        self.assertFalse(res_stranger.get_json().get("authenticated"))

        # Student A logs out
        res_logout_a = self.client.post("/api/logout", headers={"X-Session-Token": token_a})
        self.assertEqual(res_logout_a.status_code, 200)

        # Student A is now unauthenticated
        res_a_after = self.client.get("/api/status", headers={"X-Session-Token": token_a})
        self.assertFalse(res_a_after.get_json().get("authenticated"))

        # But Student B is still securely authenticated!
        res_b_after = self.client.get("/api/status", headers={"X-Session-Token": token_b})
        self.assertTrue(res_b_after.get_json().get("authenticated"))
        self.assertEqual(res_b_after.get_json()["profile"]["id"], "20110002")

    def test_per_user_cache_isolation(self):
        """Verify that Student A's schedule cached on disk is never returned to Student B."""
        from datetime import datetime
        event_a = CalendarEvent(
            uid="class_student_A_101",
            title="Giải tích 1",
            start_datetime=datetime(2026, 11, 1, 7, 30),
            end_datetime=datetime(2026, 11, 1, 11, 30),
            event_type="CLASS",
        )
        save_feed_cache([event_a], mssv="20110001")

        event_b = CalendarEvent(
            uid="class_student_B_202",
            title="Vật lý đại cương",
            start_datetime=datetime(2026, 11, 2, 13, 0),
            end_datetime=datetime(2026, 11, 2, 17, 0),
            event_type="CLASS",
        )
        save_feed_cache([event_b], mssv="20110002")

        # Load for Student A
        events_a = load_feed_cache(mssv="20110001")
        self.assertEqual(len(events_a), 1)
        self.assertEqual(events_a[0].uid, "class_student_A_101")
        self.assertEqual(events_a[0].title, "Giải tích 1")

        # Load for Student B
        events_b = load_feed_cache(mssv="20110002")
        self.assertEqual(len(events_b), 1)
        self.assertEqual(events_b[0].uid, "class_student_B_202")
        self.assertEqual(events_b[0].title, "Vật lý đại cương")

        # Load for a new student with no cached data
        events_c = load_feed_cache(mssv="99999999")
        self.assertEqual(len(events_c), 0)

    def test_cache_control_headers_and_logout_cookie(self):
        """Verify strict no-cache headers on HTML/API and cookie deletion on logout."""
        # Check /api/status headers
        res = self.client.get("/api/status")
        self.assertIn("no-store", res.headers.get("Cache-Control", ""))
        self.assertIn("max-age=0", res.headers.get("Cache-Control", ""))

        # Check / headers
        res_index = self.client.get("/")
        self.assertIn("no-store", res_index.headers.get("Cache-Control", ""))
        self.assertIn("max-age=0", res_index.headers.get("Cache-Control", ""))
        res_index.close()

        # Check logout clears cookie
        res_logout = self.client.post("/api/logout")
        self.assertEqual(res_logout.status_code, 200)
        # Verify Set-Cookie has deleted or expired hcmute_session
        cookie_header = res_logout.headers.get("Set-Cookie", "")
        self.assertIn("hcmute_session=", cookie_header)

    def test_google_login_endpoint(self):
        """Verify /api/login/google handles missing or invalid token appropriately."""
        res_empty = self.client.post("/api/login/google", json={})
        self.assertEqual(res_empty.status_code, 400)
        self.assertFalse(res_empty.get_json()["success"])

        res_invalid = self.client.post("/api/login/google", json={"credential": "invalid_dummy_token"})
        self.assertEqual(res_invalid.status_code, 401)
        self.assertFalse(res_invalid.get_json()["success"])

    def test_concurrent_session_access(self):
        """Simulate concurrent threads writing and reading sessions to prove atomic file operations."""
        import concurrent.futures
        from src.web_server import load_sessions

        def worker(idx):
            token = f"stress_test_token_{idx}"
            ACTIVE_SESSIONS[token] = {
                "session_token": token,
                "mssv": f"201100{idx:02d}",
                "name": f"Sinh Viên {idx}",
                "portal_token": f"jwt_{idx}",
            }
            save_sessions()
            load_sessions()
            return token in ACTIVE_SESSIONS

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(worker, range(20)))

        self.assertTrue(all(results))
        self.assertGreaterEqual(len(ACTIVE_SESSIONS), 20)


if __name__ == "__main__":
    unittest.main()
