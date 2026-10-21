"""
Unit tests for Feedback & Suggestion System with 10-Level AI Evaluation
"""

import unittest
import os
import sys
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.web_server import app, ACTIVE_SESSIONS, save_sessions
from src.feedback_manager import (
    evaluate_feedback,
    heuristic_evaluate,
    submit_feedback,
    get_student_feedbacks,
    get_admin_feedbacks_overview,
    update_feedback_status,
    delete_feedback,
    purge_spam_feedbacks,
    re_evaluate_feedback,
    FEEDBACKS_FILE,
)


class TestFeedbackSystem(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        # Clean feedbacks test file
        if os.path.exists(FEEDBACKS_FILE):
            os.remove(FEEDBACKS_FILE)

        # Create student session
        self.token = "test_feedback_session_token_123"
        ACTIVE_SESSIONS[self.token] = {
            "session_token": self.token,
            "mssv": "20110001",
            "name": "Nguyễn Văn A",
            "role": "SV",
            "status": "Còn học",
            "portal_token": "mock_jwt",
        }
        save_sessions()

    def tearDown(self):
        if os.path.exists(FEEDBACKS_FILE):
            os.remove(FEEDBACKS_FILE)

    def test_10_level_heuristic_evaluation(self):
        """Verify heuristic maps correctly across the 10 levels from Cần thiết to Spam."""
        # Level 1: Khẩn cấp / Sập / Bảo mật
        res1 = heuristic_evaluate("bug", "Sập server không tải được TKB", "Toàn bộ sinh viên không vào được")
        self.assertEqual(res1["level"], 1)

        # Level 2: Lỗi chức năng / Thiếu môn
        res2 = heuristic_evaluate("bug", "Bị lệch tiết học môn Giải tích", "Thời khóa biểu sai phòng học và sai giờ")
        self.assertEqual(res2["level"], 2)

        # Level 3: Tính năng giá trị cao
        res3 = heuristic_evaluate("feature", "Thêm widget đếm ngược ngày thi trên iPhone", "Mong có widget ngoài màn hình khóa")
        self.assertEqual(res3["level"], 3)

        # Level 4: UI / UX
        res4 = heuristic_evaluate("ui", "Tối ưu giao diện dark mode và chữ nhỏ", "Giao diện trên điện thoại hơi lag và font chữ nhỏ")
        self.assertEqual(res4["level"], 4)

        # Level 7: Thắc mắc / Đã có
        res7 = heuristic_evaluate("other", "Làm sao để đồng bộ vào Google Calendar?", "Cho em hỏi cách nào đồng bộ vào Android với ạ")
        self.assertEqual(res7["level"], 7)

        # Level 9: Ngoài phạm vi
        res9 = heuristic_evaluate("other", "Hack sửa điểm môn Triết", "Nhờ admin sửa điểm thi môn này giúp em")
        self.assertEqual(res9["level"], 9)

        # Level 10: Spam / Rác
        res10 = heuristic_evaluate("other", "asdfghjkl", "123456 test thử")
        self.assertEqual(res10["level"], 10)

    def test_submit_and_student_my_feedback(self):
        """Submit feedback through API and retrieve in student history."""
        # Submit valid feedback
        res = self.client.post(
            "/api/feedback/submit",
            headers={"X-Session-Token": self.token},
            json={
                "category": "feature",
                "title": "Tích hợp nhắc nhở trước giờ thi 1 ngày",
                "content": "Em muốn hệ thống gửi thông báo nhắc trước ngày thi để chuẩn bị thẻ sinh viên",
                "is_anonymous": False,
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))
        fb = data.get("feedback")
        self.assertEqual(fb["mssv"], "20110001")
        self.assertEqual(fb["author_name"], "Nguyễn Văn A")
        self.assertIn("level", fb["ai_evaluation"])

        # Fetch my feedback
        res_my = self.client.get("/api/feedback/my", headers={"X-Session-Token": self.token})
        self.assertEqual(res_my.status_code, 200)
        data_my = res_my.get_json()
        self.assertTrue(data_my.get("success"))
        self.assertEqual(len(data_my.get("feedbacks", [])), 1)

    def test_admin_feedbacks_management_and_spam_purge(self):
        """Verify admin overview, status updates, and spam purging."""
        from src.telegram_bot import load_config
        admin_key = os.environ.get("ADMIN_PASSCODE") or load_config().get("admin_passcode") or "admin123"

        # Submit a normal feedback and a spam feedback
        fb_valid = submit_feedback("20110001", "Nguyễn Văn A", "feature", "Widget iOS", "Thêm widget iOS rất tiện")
        fb_spam = submit_feedback("20110002", "Spam User", "other", "asdfgh", "123456 test")

        # Admin fetches list
        res = self.client.get(f"/api/admin/feedbacks?admin_key={admin_key}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["stats"]["spam_count"], 1)

        # Admin updates status
        res_up = self.client.post(
            f"/api/admin/feedback/status?admin_key={admin_key}",
            json={"id": fb_valid["id"], "status": "resolved", "admin_note": "Đã hoàn thành trong v2.2!"},
        )
        self.assertEqual(res_up.status_code, 200)

        # Admin purges spam
        res_purge = self.client.post(f"/api/admin/feedback/purge-spam?admin_key={admin_key}")
        self.assertEqual(res_purge.status_code, 200)
        self.assertEqual(res_purge.get_json()["removed"], 1)

        # Verify only valid feedback remains
        feedbacks_after = get_student_feedbacks("20110001")
        self.assertEqual(len(feedbacks_after), 1)
        self.assertEqual(feedbacks_after[0]["status"], "resolved")
        self.assertEqual(feedbacks_after[0]["admin_note"], "Đã hoàn thành trong v2.2!")


if __name__ == "__main__":
    unittest.main()
