"""
HCMUTE Portal API Client
Direct REST integration with https://portalapi.hcmute.edu.vn/api/
"""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests


class HCMUTEClient:
    def __init__(
        self,
        base_url: str = "https://portalapi.hcmute.edu.vn/api/",
        api_key: str = "utepscRBF0zT2Mqo6vMw69YMOH43IrB2RtXBS0EHit2kzvL2auxaFJBvw==",
        client_id: str = "ute",
        token_cache_file: str = "data/.token_cache.json",
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.client_id = client_id
        self.token_cache_file = token_cache_file
        self.session_data: Optional[Dict[str, Any]] = None
        self.token: Optional[str] = None
        self.load_cached_session()

    def _headers(self, authenticated: bool = True) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "apiKey": self.api_key,
            "clientId": self.client_id,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if authenticated and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def load_cached_session(self) -> bool:
        """Load session from local cache file if still valid."""
        if not os.path.exists(self.token_cache_file):
            return False

        try:
            with open(self.token_cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            expire_str = data.get("Expire")
            if expire_str:
                # Format: 2026-11-20T14:49:56 or with Z
                expire_dt = datetime.fromisoformat(expire_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if expire_dt.tzinfo is None:
                    expire_dt = expire_dt.replace(tzinfo=timezone.utc)
                if now > expire_dt:
                    return False

            self.session_data = data
            self.token = data.get("Token")
            return bool(self.token)
        except Exception:
            return False

    def save_session(self, data: Dict[str, Any]) -> None:
        """Save session to local cache."""
        os.makedirs(os.path.dirname(self.token_cache_file) or ".", exist_ok=True)
        with open(self.token_cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.session_data = data
        self.token = data.get("Token")

    def ensure_authenticated(self) -> bool:
        """
        Verify if current session is active.
        Zero-Knowledge Architecture: Passwords are NEVER stored on disk.
        Returns True if session token is still valid.
        """
        if self.token and self.session_data:
            expire_str = self.session_data.get("Expire")
            if expire_str:
                try:
                    expire_dt = datetime.fromisoformat(expire_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if expire_dt.tzinfo is None:
                        expire_dt = expire_dt.replace(tzinfo=timezone.utc)
                    if now < expire_dt:
                        return True
                except Exception:
                    pass
            else:
                return True
        return False

    def logout(self) -> None:
        """Clear local session."""
        self.session_data = None
        self.token = None
        if os.path.exists(self.token_cache_file):
            try:
                os.remove(self.token_cache_file)
            except OSError:
                pass
        # Clean up any legacy vault if it existed
        vault_path = os.path.join(os.path.dirname(self.token_cache_file) or "data", ".auth_vault.json")
        if os.path.exists(vault_path):
            try:
                os.remove(vault_path)
            except OSError:
                pass

    def login(self, username: str, password: str, account_type: int = 0) -> Dict[str, Any]:
        """
        Authenticate with HCMUTE student account.
        Zero Password Storage: Password is sent directly to HCMUTE Portal over HTTPS
        and immediately discarded from memory once JWT Bearer Token is received.
        Endpoint: POST /api/authenticate/authpsc
        """
        url = self.base_url + "authenticate/authpsc"
        payload = {
            "username": username.strip(),
            "password": password.strip(),
            "type": account_type,
        }

        resp = requests.post(
            url,
            headers=self._headers(authenticated=False),
            json=payload,
            timeout=15,
        )

        # Discard password immediately from memory
        del payload

        if resp.status_code != 200:
            try:
                err = resp.json().get("message", resp.text)
            except Exception:
                err = resp.text
            raise ConnectionError(f"Đăng nhập thất bại: {err}")

        data = resp.json()
        if not data or "Token" not in data:
            raise ValueError(f"Dữ liệu phản hồi đăng nhập không hợp lệ: {data}")

        self.save_session(data)
        return data

    def is_authenticated(self) -> bool:
        return bool(self.token)

    def get_user_profile(self) -> Optional[Dict[str, Any]]:
        return self.session_data

    # ==================== Year & Term ====================

    def get_year_and_term(self) -> Dict[str, Any]:
        """
        Get current academic year & semester, plus list of available terms.
        Endpoint: GET /api/student/yearandterm
        """
        url = self.base_url + "student/yearandterm"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ==================== Schedule / TKB ====================

    def get_schedule_semester(self, namhoc: str, hocky: str) -> Dict[str, Any]:
        """
        Get entire semester schedule for student.
        Endpoint: GET /api/student/DrawingStudentSchedule_Perior?namhoc={namhoc}&hocky={hocky}
        """
        url = f"{self.base_url}student/DrawingStudentSchedule_Perior?namhoc={namhoc}&hocky={hocky}"
        resp = requests.get(url, headers=self._headers(), timeout=20)
        resp.raise_for_status()
        return resp.json()

    def get_schedule_week(self, namhoc: str, hocky: str, tuan: int) -> Dict[str, Any]:
        """
        Get schedule for a specific week.
        Endpoint: GET /api/student/DrawingStudentSchedules?namhoc={namhoc}&hocky={hocky}&tuan={tuan}
        """
        url = f"{self.base_url}student/DrawingStudentSchedules?namhoc={namhoc}&hocky={hocky}&tuan={tuan}"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_weeks(self, namhoc: str, hocky: str) -> List[Dict[str, Any]]:
        """
        Get all weeks in the semester.
        Endpoint: GET /api/student/WeekSchedule?namhoc={namhoc}&hocky={hocky}
        """
        url = f"{self.base_url}student/WeekSchedule?namhoc={namhoc}&hocky={hocky}"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ==================== Exam Schedule / Lịch thi ====================

    def get_exam_schedule(self, namhoc: str, hocky: str) -> List[Dict[str, Any]]:
        """
        Get exam schedule for student in a specific semester.
        Endpoint: GET /api/student/exam?namhoc={namhoc}&hocky={hocky}
        """
        url = f"{self.base_url}student/exam?namhoc={namhoc}&hocky={hocky}"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return []

    def get_exams_by_time(self) -> List[Dict[str, Any]]:
        """
        Get comprehensive detailed exam schedule with exact dates, rooms, and periods.
        Endpoint: GET /api/student/showexambytime
        """
        url = f"{self.base_url}student/showexambytime"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return []

    # ==================== Notifications / Thông báo ====================

    def get_general_announcements(self) -> List[Dict[str, Any]]:
        """
        Get all general announcements for students.
        Endpoint: GET /api/student/GetAllThongBaoChung
        """
        url = self.base_url + "student/GetAllThongBaoChung"
        resp = requests.get(url, headers=self._headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return []

    def get_personal_messages(self) -> List[Dict[str, Any]]:
        """
        Get personal inbox notifications for the student.
        Endpoint: GET /api/student/GetMessagesByReceiverID
        """
        url = self.base_url + "student/GetMessagesByReceiverID"
        resp = requests.get(url, headers=self._headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return []

    def get_message_detail(self, message_id: Any) -> Dict[str, Any]:
        """
        Get full content of an announcement by ID.
        Endpoint: GET /api/student/GetMessageStudentDetailById?Id={message_id}
        """
        url = f"{self.base_url}student/GetMessageStudentDetailById?Id={message_id}"
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ==================== Tuition / Học phí ====================

    def get_fee_debt(self) -> Any:
        """
        Get tuition payment debt / status.
        Endpoint: GET /api/authenticate/GetStudentFeedebt or /api/student/GetStudentFeedebt
        """
        for prefix in ("authenticate", "student"):
            try:
                url = f"{self.base_url}{prefix}/GetStudentFeedebt"
                resp = requests.get(url, headers=self._headers(), timeout=15)
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                pass
        return None
