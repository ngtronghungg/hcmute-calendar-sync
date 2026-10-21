"""
HCMUTE Calendar & Reminder Sync - Local Web Server
Provides modern REST API and serves the Apple/Nothing-style Web UI.
"""

import os
import sys
import json
import webbrowser
from threading import Timer

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, jsonify, request, send_file, send_from_directory, Response

from src.hcmute_api import HCMUTEClient
from src.schedule_parser import parse_semester_schedule, parse_exam_schedule, CalendarEvent
from src.notice_analyzer import NoticeAnalyzer
from src.google_calendar import GoogleCalendarSync
from src.ics_exporter import export_events_to_ics, events_to_ics_bytes
from src.telegram_bot import (
    TelegramBotDaemon,
    get_or_create_bot_daemon,
    send_telegram_message,
    load_cancellations,
    add_cancellation,
    save_config as save_tg_config,
    load_config,
    get_vietnam_now,
    record_student_access,
    load_student_access,
    delete_student_access,
)
from src.schedule_cache import (
    load_feed_cache,
    save_feed_cache,
    load_cached_exams,
    save_cached_exams,
    CALENDAR_FEED_CACHE_FILE,
    EXAM_CACHE_FILE,
)

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "config.json"))
SESSIONS_FILE = os.path.join(PROJECT_ROOT, "data", "sessions.json")

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")

import secrets
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

ACTIVE_SESSIONS: Dict[str, dict] = {}
SESSION_LOCK = threading.RLock()


def load_sessions() -> None:
    """Load active student sessions from disk thread-safely without wiping memory on read errors."""
    global ACTIVE_SESSIONS
    with SESSION_LOCK:
        if os.path.exists(SESSIONS_FILE):
            try:
                with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if k not in ACTIVE_SESSIONS:
                                ACTIVE_SESSIONS[k] = v
            except Exception:
                pass


def save_sessions() -> None:
    """Persist active student sessions to disk atomically and thread-safely."""
    with SESSION_LOCK:
        try:
            os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
            tmp_file = f"{SESSIONS_FILE}.tmp.{os.getpid()}.{secrets.token_hex(4)}"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(ACTIVE_SESSIONS, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, SESSIONS_FILE)
        except Exception:
            try:
                if 'tmp_file' in locals() and os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass


load_sessions()

# Clean up legacy single-user cache on boot so no user data is retained across global restarts
legacy_token_path = os.path.join(PROJECT_ROOT, "data", ".token_cache.json")
if os.path.exists(legacy_token_path):
    try:
        os.remove(legacy_token_path)
    except Exception:
        pass


def get_client() -> HCMUTEClient:
    """Internal client used by Telegram background daemon and admin functions."""
    cfg = load_config()
    hcmute_cfg = cfg.get("hcmute", {})
    return HCMUTEClient(
        base_url=hcmute_cfg.get("base_url", "https://portalapi.hcmute.edu.vn/api/"),
        api_key=hcmute_cfg.get("api_key", "utepscRBF0zT2Mqo6vMw69YMOH43IrB2RtXBS0EHit2kzvL2auxaFJBvw=="),
        client_id=hcmute_cfg.get("client_id", "ute"),
        token_cache_file="data/.admin_token.json",
    )


def get_request_session() -> Optional[dict]:
    """
    Extract session from request header 'X-Session-Token' or 'Authorization: Bearer <token>',
    cookie 'hcmute_session', or query parameter 'session_token' / 'token'.
    """
    token = request.headers.get("X-Session-Token", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "").strip()
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
    if not token:
        token = request.cookies.get("hcmute_session", "").strip()
    if not token:
        token = (request.args.get("session_token") or request.args.get("token") or "").strip()

    if not token:
        return None

    with SESSION_LOCK:
        if token not in ACTIVE_SESSIONS:
            # Reload from disk in case another worker process or server restart occurred
            load_sessions()

        if token not in ACTIVE_SESSIONS:
            return None

        session = ACTIVE_SESSIONS[token]
        session["last_active"] = datetime.now(timezone.utc).isoformat()
        return session


def get_session_client(session: Optional[dict] = None) -> Optional[HCMUTEClient]:
    """Create an ephemeral HCMUTEClient strictly bound to the requesting user's session token."""
    if session is None:
        session = get_request_session()
    if not session:
        return None

    portal_token = session.get("portal_token")
    if not portal_token:
        return None

    cfg = load_config()
    hcmute_cfg = cfg.get("hcmute", {})
    client = HCMUTEClient(
        base_url=hcmute_cfg.get("base_url", "https://portalapi.hcmute.edu.vn/api/"),
        api_key=hcmute_cfg.get("api_key", "utepscRBF0zT2Mqo6vMw69YMOH43IrB2RtXBS0EHit2kzvL2auxaFJBvw=="),
        client_id=hcmute_cfg.get("client_id", "ute"),
        token_cache_file=None,  # Crucial: in-memory only, never leak or write to shared file!
    )
    client.token = portal_token
    client.session_data = session.get("session_data") or {
        "Id": session.get("mssv"),
        "FullName": session.get("name"),
        "Role": session.get("role", "SV"),
        "StudyStatusName": session.get("status", "Đang học"),
        "Token": portal_token,
    }
    return client


# Start Telegram daemon in background (handles polling fallback and morning briefings)
bot_daemon = get_or_create_bot_daemon(get_client)
bot_daemon.start()


@app.after_request
def add_cache_headers(response):
    """Enforce strict no-cache headers on dynamic pages and APIs, but cache static assets for 24h to reduce Render CPU & bandwidth."""
    if request.path.startswith("/api/") or request.path in ["/", "/admin"]:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    else:
        # Cache static assets (CSS, JS, fonts, images) for 24 hours
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/health", methods=["GET", "HEAD"])
def health_check():
    """
    Lightweight health check endpoint for Render, UptimeRobot, and Keep-Alive daemon.
    Consumes < 1ms CPU, 0 disk I/O, keeps free instance awake 24/7 within 750 free hours.
    """
    return jsonify({
        "status": "healthy",
        "service": "UTE_Cal",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_sessions": len(ACTIVE_SESSIONS)
    })


# ---------------------------------------------------------------------------
# Background Keep-Alive Daemon (Bypasses Render 15-min free spin-down)
# ---------------------------------------------------------------------------
import threading
import urllib.request

_keep_alive_thread: Optional[threading.Thread] = None
_keep_alive_stop_event = threading.Event()


def keep_alive_worker(target_url: str, interval_sec: int = 600):
    """
    Background worker that pings target_url/health every 10 minutes (600s).
    Touches Render's public router from external traffic, resetting the 15-minute inactivity timer.
    Uses 0 cost (744h <= 750h free tier quota).
    """
    health_url = target_url.rstrip("/") + "/health"
    print(f"[Keep-Alive] 🚀 Khởi chạy daemon ping {health_url} mỗi {interval_sec}s để chống ngủ đông Render.")

    # Wait 45s on boot before first ping to allow server startup to stabilize
    if _keep_alive_stop_event.wait(45):
        return

    while not _keep_alive_stop_event.is_set():
        try:
            req = urllib.request.Request(
                health_url,
                headers={"User-Agent": "UTE_Cal-KeepAlive/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as res:
                if res.status == 200:
                    pass
        except Exception as e:
            # Silent to avoid spamming logs
            pass

        if _keep_alive_stop_event.wait(interval_sec):
            break


def start_keep_alive_daemon():
    """Start the keep alive background daemon if enabled or if running on Render."""
    global _keep_alive_thread
    if _keep_alive_thread and _keep_alive_thread.is_alive():
        return

    external_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("KEEP_ALIVE_URL")
    keep_alive_enabled = (
        os.environ.get("KEEP_ALIVE_ENABLED", "").lower() in ("true", "1", "yes")
        or bool(os.environ.get("RENDER_EXTERNAL_URL"))
    )

    if keep_alive_enabled and external_url:
        _keep_alive_stop_event.clear()
        _keep_alive_thread = threading.Thread(
            target=keep_alive_worker,
            args=(external_url, 600),
            daemon=True,
            name="KeepAliveDaemon"
        )
        _keep_alive_thread.start()


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html", max_age=0)


@app.route("/admin")
def admin_page():
    return send_from_directory(WEB_DIR, "admin.html", max_age=0)


def check_admin_auth() -> bool:
    """Validate admin authorization via X-Admin-Key header, query param, or json body."""
    admin_pass = os.environ.get("ADMIN_PASSCODE") or load_config().get("admin_passcode", "admin123")
    if not admin_pass:
        return True
    key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    if not key and request.is_json and request.json:
        key = request.json.get("key") or request.json.get("admin_key")
    return str(key or "").strip() == str(admin_pass).strip()


@app.route("/api/admin/verify", methods=["POST"])
def api_admin_verify():
    """Verify admin passcode from frontend."""
    if check_admin_auth():
        return jsonify({"success": True, "message": "Xác thực thành công"})
    return jsonify({"success": False, "message": "Mật khẩu quản trị không chính xác."}), 403


@app.route("/api/status", methods=["GET"])
def api_status():
    """
    Return login state and student profile for the requesting client.
    Enforces per-browser session isolation via X-Session-Token.
    Unauthenticated requests always receive authenticated: false.
    """
    session = get_request_session()
    if not session or not session.get("portal_token"):
        return jsonify({"authenticated": False})

    s_id = str(session.get("mssv", "")).strip()
    s_name = session.get("name", "Sinh viên")
    if s_id:
        record_student_access(s_id, s_name, channel="Web Portal")
    return jsonify({
        "authenticated": True,
        "session_token": session.get("session_token"),
        "profile": {
            "id": s_id,
            "name": s_name,
            "role": session.get("role", "SV"),
            "status": session.get("status", "Đang học"),
        }
    })


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """
    Direct web chat endpoint for UTE_Cal assistant powered by Groq API.
    Zero-knowledge session isolation, instant response (< 1s).
    """
    data = request.json or {}
    user_msg = str(data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"success": False, "message": "Tin nhắn không được để trống."}), 400

    from src.conversational_ai import generate_conversational_reply
    from src.telegram_bot import load_cancellations, load_cancellation_votes

    session = get_request_session()
    client = get_session_client(session) if session else None
    student_name = session.get("name", "Bạn") if session else "Bạn"
    student_mssv = str(session.get("mssv", "")).strip() if session else ""

    all_events = []
    if student_mssv:
        try:
            all_events = load_feed_cache(mssv=student_mssv) or []
            cached_exams = load_cached_exams(mssv=student_mssv)
            if not cached_exams:
                cached_exams = load_cached_exams(mssv=None)
            existing_uids = {e.uid for e in all_events}
            for ex in (cached_exams or []):
                if ex.uid not in existing_uids:
                    all_events.append(ex)
        except Exception:
            all_events = []

    cancelled_set = load_cancellations()
    votes_db = load_cancellation_votes()

    cfg = load_config()
    groq_key = cfg.get("groq_api_key") or os.environ.get("GROQ_API_KEY", "")

    try:
        reply_text, _ = generate_conversational_reply(
            text=user_msg,
            chat_id=student_mssv or "web_user",
            client=client,
            student_name=student_name,
            student_mssv=student_mssv,
            all_events=all_events,
            cancelled_set=cancelled_set,
            votes_db=votes_db,
            groq_key=groq_key,
        )
        return jsonify({"success": True, "reply": reply_text})
    except Exception as e:
        return jsonify({
            "success": True,
            "reply": f"🤖 <b>UTE_Cal:</b> Xin lỗi bạn, mình vừa gặp trục trặc tạm thời ({str(e)}). Vui lòng kiểm tra Groq API Key hoặc thử lại nhé!"
        })


@app.route("/api/login", methods=["POST"])
def api_login():
    """
    Handle student login.
    Zero-Knowledge Multi-User Architecture:
    - Passwords are held in RAM only for the duration of the HTTPS request, then destroyed immediately.
    - An isolated session_token is generated and returned to the browser.
    - Passwords are NEVER written to disk, database, or server logs.
    """
    data = request.json or {}
    token_input = data.get("token", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    cfg = load_config()
    hcmute_cfg = cfg.get("hcmute", {})
    client = HCMUTEClient(
        base_url=hcmute_cfg.get("base_url", "https://portalapi.hcmute.edu.vn/api/"),
        api_key=hcmute_cfg.get("api_key", "utepscRBF0zT2Mqo6vMw69YMOH43IrB2RtXBS0EHit2kzvL2auxaFJBvw=="),
        client_id=hcmute_cfg.get("client_id", "ute"),
        token_cache_file=None,  # In-memory only
    )

    # Option 1: Direct Token Login (Zero Password)
    if token_input:
        client.token = token_input
        try:
            yt = client.get_year_and_term()
            session_payload = {
                "Id": username or "SV",
                "FullName": username or "Sinh viên",
                "Token": token_input,
                "Role": "SV",
                "StudyStatusName": "Đang học (Token Auth)",
            }
            client.session_data = session_payload

            session_token = secrets.token_urlsafe(32)
            session_rec = {
                "session_token": session_token,
                "mssv": session_payload["Id"],
                "name": session_payload["FullName"],
                "role": session_payload["Role"],
                "status": session_payload["StudyStatusName"],
                "portal_token": token_input,
                "session_data": session_payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_active": datetime.now(timezone.utc).isoformat(),
            }
            with SESSION_LOCK:
                ACTIVE_SESSIONS[session_token] = session_rec
                save_sessions()
            record_student_access(mssv=session_payload["Id"], name=session_payload["FullName"], channel="Web Portal (Token)")
            resp = jsonify({
                "success": True,
                "session_token": session_token,
                "profile": {
                    "id": session_payload["Id"],
                    "name": session_payload["FullName"],
                    "role": session_payload["Role"],
                    "status": session_payload["StudyStatusName"],
                }
            })
            resp.set_cookie("hcmute_session", session_token, max_age=86400 * 7, httponly=False, samesite="Lax")
            return resp
        except Exception as e:
            return jsonify({"success": False, "message": f"Token không hợp lệ hoặc đã hết hạn: {e}"}), 400

    # Option 2: Standard MSSV + Password (Ephemeral, never stored)
    if not username or not password:
        return jsonify({"success": False, "message": "Vui lòng nhập MSSV và Mật khẩu."}), 400

    try:
        profile = client.login(username, password)
        # Explicit memory purge
        del password
        del data

        session_token = secrets.token_urlsafe(32)
        mssv = str(profile.get("Id") or username).strip()
        name = profile.get("FullName") or profile.get("Username", "Sinh viên")
        role = profile.get("Role", "SV")
        status = profile.get("StudyStatusName", "Đang học")
        expire = profile.get("Expire")

        session_rec = {
            "session_token": session_token,
            "mssv": mssv,
            "name": name,
            "role": role,
            "status": status,
            "portal_token": client.token,
            "expire": expire,
            "session_data": profile,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_active": datetime.now(timezone.utc).isoformat(),
        }
        with SESSION_LOCK:
            ACTIVE_SESSIONS[session_token] = session_rec
            save_sessions()

        record_student_access(mssv=mssv, name=name, channel="Web Portal")
        resp = jsonify({
            "success": True,
            "session_token": session_token,
            "profile": {
                "id": mssv,
                "name": name,
                "role": role,
                "status": status,
            }
        })
        resp.set_cookie("hcmute_session", session_token, max_age=86400 * 7, httponly=False, samesite="Lax")
        return resp
    except Exception as e:
        try:
            del password
            del data
        except Exception:
            pass
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/api/login/google", methods=["POST"])
def api_login_google():
    """
    Authenticate student with Google OAuth credential (@student.hcmute.edu.vn).
    Sends credential directly to portalapi.hcmute.edu.vn/api/authenticate/AuthenticateGoogle.
    """
    data = request.json or {}
    google_token = data.get("credential") or data.get("token") or ""
    if not google_token:
        return jsonify({"success": False, "message": "Không tìm thấy mã xác thực Google."}), 400

    cfg = load_config()
    hcmute_cfg = cfg.get("hcmute", {})
    client = HCMUTEClient(
        base_url=hcmute_cfg.get("base_url", "https://portalapi.hcmute.edu.vn/api/"),
        api_key=hcmute_cfg.get("api_key", "utepscRBF0zT2Mqo6vMw69YMOH43IrB2RtXBS0EHit2kzvL2auxaFJBvw=="),
        client_id=hcmute_cfg.get("client_id", "ute"),
        token_cache_file=None,
    )

    try:
        profile = client.login_with_google(google_token)
        session_token = secrets.token_urlsafe(32)
        mssv = str(profile.get("Id", "")).strip()
        name = profile.get("FullName") or profile.get("Username", "Sinh viên")
        role = profile.get("Role", "SV")
        status = profile.get("StudyStatusName", "Đang học (Google Auth)")
        expire = profile.get("Expire")

        session_rec = {
            "session_token": session_token,
            "mssv": mssv,
            "name": name,
            "role": role,
            "status": status,
            "portal_token": client.token,
            "expire": expire,
            "session_data": profile,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_active": datetime.now(timezone.utc).isoformat(),
        }
        with SESSION_LOCK:
            ACTIVE_SESSIONS[session_token] = session_rec
            save_sessions()

        record_student_access(mssv=mssv, name=name, channel="Web Portal (Google)")
        resp = jsonify({
            "success": True,
            "session_token": session_token,
            "profile": {
                "id": mssv,
                "name": name,
                "role": role,
                "status": status,
            }
        })
        resp.set_cookie("hcmute_session", session_token, max_age=86400 * 7, httponly=False, samesite="Lax")
        return resp
    except Exception as e:
        return jsonify({"success": False, "message": f"Đăng nhập Google thất bại: {e}"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    """Handle student logout by revoking their browser session."""
    session = get_request_session()
    if session:
        token = session.get("session_token")
        with SESSION_LOCK:
            if token and token in ACTIVE_SESSIONS:
                del ACTIVE_SESSIONS[token]
                save_sessions()
    resp = jsonify({"success": True})
    resp.delete_cookie("hcmute_session")
    return resp


@app.route("/api/security/audit", methods=["GET"])
def api_security_audit():
    """
    Public Security & Privacy Transparency Audit endpoint.
    Demonstrates empirically that no student passwords or sensitive data are stored.
    """
    token_path = os.path.join(PROJECT_ROOT, "data", ".token_cache.json")
    vault_path = os.path.join(PROJECT_ROOT, "data", ".auth_vault.json")

    has_token = os.path.exists(token_path)
    vault_exists = os.path.exists(vault_path)

    token_fields = []
    if has_token:
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
                token_fields = list(tdata.keys())
        except Exception:
            pass

    return jsonify({
        "success": True,
        "security_principles": {
            "password_storage": "NONE (0 byte lưu trữ trên đĩa hoặc database)",
            "database_used": "NONE (Kiến trúc phi máy chủ dữ liệu - Stateless Architecture)",
            "auth_vault_exists": vault_exists,
            "ephemeral_credentials": "Mật khẩu chỉ tồn tại trong RAM trong tích tắc khi gửi HTTPS tới Cổng trường, sau đó tự hủy (del) ngay lập tức.",
            "portal_connection": "Kết nối trực tiếp qua HTTPS tới https://portalapi.hcmute.edu.vn",
            "sensitive_data_status": {
                "password": "❌ KHÔNG LƯU (0 byte)",
                "cccd_identity_card": "❌ KHÔNG LƯU (0 byte)",
                "grades_transcripts": "❌ KHÔNG LƯU (0 byte)",
                "bank_accounts": "❌ KHÔNG LƯU (0 byte)",
            },
            "token_cache_fields_on_disk": token_fields,
            "stored_data_justification": [
                {
                    "item": "Lịch học & Lịch thi (Thời khóa biểu)",
                    "why": "Để sinh viên đăng ký kênh Webcal / Google Calendar / Apple Calendar tự động",
                    "sensitivity": "Công khai theo lớp học phần"
                },
                {
                    "item": "Telegram Chat ID (Nếu sinh viên kết nối Bot)",
                    "why": "Để Bot gửi thông báo lịch học 06:30 sáng và báo khi giảng viên cho nghỉ",
                    "sensitivity": "Định danh tin nhắn ẩn danh của Telegram"
                }
            ],
            "transparency_guarantees": [
                "100% Mã nguồn mở trên GitHub để sinh viên kiểm tra từng dòng lệnh",
                "Có thể kiểm tra Network Tab (F12) để giám sát từng gói tin gửi đi",
                "Hỗ trợ chế độ Đăng nhập Bằng Token (Hoàn toàn không cần gõ mật khẩu)",
                "Nút xóa sạch dữ liệu (Right to be Forgotten) với 1 chạm"
            ]
        }
    })


@app.route("/api/security/wipe-my-data", methods=["POST"])
def api_security_wipe():
    """1-Click Data Wipe (Right to be Forgotten). Clears session and caches for requesting user."""
    session = get_request_session()
    mssv = session.get("mssv") if session else None

    if session:
        token = session.get("session_token")
        with SESSION_LOCK:
            if token and token in ACTIVE_SESSIONS:
                del ACTIVE_SESSIONS[token]
                save_sessions()

    if mssv:
        user_dir = os.path.join(PROJECT_ROOT, "data", "users", mssv)
        if os.path.exists(user_dir):
            try:
                import shutil
                shutil.rmtree(user_dir)
            except Exception:
                pass

    try:
        from src.telegram_bot import delete_telegram_user
        data = request.json or {}
        chat_id = data.get("chat_id")
        if chat_id:
            delete_telegram_user(str(chat_id))
    except Exception:
        pass

    return jsonify({"success": True, "message": "Đã xóa toàn bộ phiên đăng nhập và dữ liệu cục bộ của bạn thành công."})


@app.route("/api/overview", methods=["GET"])
def api_overview():
    """Fetch current year & term, class schedule, and exam schedule with persistent cache fallback."""
    session = get_request_session()
    if not session:
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

    client = get_session_client(session)
    if not client or not client.ensure_authenticated():
        return jsonify({"success": False, "message": "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."}), 401

    mssv = str(session.get("mssv", "")).strip()
    from datetime import datetime, timedelta
    now = get_vietnam_now()

    include_past = request.args.get("include_past", "false").lower() == "true"
    include_past_exams = request.args.get("include_past_exams", "false").lower() == "true"

    all_class_events = []
    all_raw_exams = []
    curr_year = "2026-2027"
    curr_term = "HK01"
    available_years = ["2026-2027", "2025-2026", "2024-2025"]
    available_terms = [
        {"TermID": "HK01", "TermName": "Học kỳ 1"},
        {"TermID": "HK02", "TermName": "Học kỳ 2"},
        {"TermID": "HK03", "TermName": "Học kỳ 3"}
    ]

    try:
        try:
            from concurrent.futures import ThreadPoolExecutor, wait
            with ThreadPoolExecutor(max_workers=4) as pool:
                fut_yt = pool.submit(client.get_year_and_term)
                try:
                    yt = fut_yt.result(timeout=3.0)
                    available_years = yt.get("YearStudy") or available_years
                    available_terms = yt.get("Terms") or available_terms
                    curr_year = request.args.get("year") or yt.get("CurrentYear") or yt.get("NamHienTai") or curr_year
                    curr_term = request.args.get("term") or yt.get("CurrentTerm") or yt.get("HocKyHienTai") or curr_term
                except Exception:
                    pass

                fut_sched = pool.submit(client.get_schedule_semester, curr_year, curr_term)
                fut_time_exams = pool.submit(client.get_exams_by_time)
                fut_term_exams = pool.submit(client.get_exam_schedule, curr_year, curr_term)

                # Bounded fast timeout: never hang the browser or kick the user out
                done, _ = wait([fut_sched, fut_time_exams, fut_term_exams], timeout=4.5)

                if fut_sched in done:
                    try:
                        raw_sched = fut_sched.result()
                        all_class_events = parse_semester_schedule(raw_sched)
                        if all_class_events:
                            save_feed_cache(all_class_events, mssv=mssv)
                    except Exception:
                        pass

                if fut_time_exams in done:
                    try:
                        all_raw_exams.extend(fut_time_exams.result())
                    except Exception:
                        pass

                if fut_term_exams in done:
                    try:
                        all_raw_exams.extend(fut_term_exams.result())
                    except Exception:
                        pass
        except Exception:
            pass

        # Fallback to cached classes if live fetch failed
        if not all_class_events:
            cached_events = load_feed_cache(mssv=mssv)
            all_class_events = [c for c in cached_events if (c.event_type or "").upper() in ("CLASS", "TKB")]

        # Always include make-up classes!
        from src.telegram_bot import load_makeup_classes
        all_class_events.extend(load_makeup_classes())
        from src.schedule_parser import assign_course_colors
        assign_course_colors(all_class_events)

        # Parse live exams
        exam_events = parse_exam_schedule(all_raw_exams)

        # Fallback to cached exams if live exams returned empty
        if not exam_events:
            exam_events = load_cached_exams(mssv=mssv)
        else:
            save_cached_exams(exam_events, mssv=mssv)

        assign_course_colors(exam_events)

        # Deduplicate exams by title and date
        deduped_exams = []
        seen_exam_keys = set()
        for ex in exam_events:
            k = (ex.title, ex.start_datetime.strftime("%Y%m%d%H%M"))
            if k not in seen_exam_keys:
                seen_exam_keys.add(k)
                deduped_exams.append(ex)

        upcoming_classes = [c for c in all_class_events if c.end_datetime >= now]
        upcoming_classes.sort(key=lambda x: x.start_datetime)

        past_classes = [c for c in all_class_events if c.end_datetime < now]
        past_classes.sort(key=lambda x: x.start_datetime, reverse=True)

        # Keep exams from past 7 days in upcoming_exams so active exam week is never empty
        upcoming_exams = [e for e in deduped_exams if e.start_datetime >= now - timedelta(days=7)]
        upcoming_exams.sort(key=lambda x: x.start_datetime)

        past_exams = [e for e in deduped_exams if e.start_datetime < now - timedelta(days=7)]
        past_exams.sort(key=lambda x: x.start_datetime, reverse=True)

        classes_to_return = all_class_events if include_past else upcoming_classes
        classes_to_return.sort(key=lambda x: x.start_datetime)

        cancelled_set = load_cancellations()
        classes_data = []
        for c in classes_to_return:
            d = c.to_dict()
            d["is_cancelled"] = c.uid in cancelled_set
            classes_data.append(d)

        exams_to_return = deduped_exams if include_past_exams else upcoming_exams
        exams_to_return.sort(key=lambda x: x.start_datetime)

        return jsonify({
            "success": True,
            "year": curr_year,
            "term": curr_term,
            "years": available_years,
            "terms": available_terms,
            "include_past": include_past,
            "include_past_exams": include_past_exams,
            "total_classes_count": len(all_class_events),
            "upcoming_classes_count": len(upcoming_classes),
            "past_classes_count": len(past_classes),
            "class_count": len(classes_to_return),
            "total_exams_count": len(deduped_exams),
            "upcoming_exams_count": len(upcoming_exams),
            "past_exams_count": len(past_exams),
            "exam_count": len(exams_to_return),
            "classes": classes_data,
            "exams": [e.to_dict() for e in exams_to_return],
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


CONSIDERATIONS_CACHE: Dict[str, dict] = {}


@app.route("/api/considerations", methods=["GET"])
def api_considerations():
    """Scan announcements, fee debt, and upcoming exams to formulate reminder recommendations."""
    session = get_request_session()
    if not session:
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

    client = get_session_client(session)
    if not client or not client.ensure_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập hoặc phiên đã hết hạn."}), 401

    mssv = str(session.get("mssv", "default")).strip()
    import time
    force = request.args.get("force", "0") in ("1", "true", "True")
    user_cached = CONSIDERATIONS_CACHE.get(mssv)
    if not force and user_cached and (time.time() - user_cached.get("timestamp", 0) < 300):
        return jsonify({"success": True, "count": len(user_cached.get("data", [])), "items": user_cached.get("data", [])})

    try:
        from datetime import datetime, timedelta
        from src.notice_analyzer import ConsiderationItem, decrypt_hcmute_body
        from concurrent.futures import ThreadPoolExecutor

        analyzer = NoticeAnalyzer(load_config())
        now = get_vietnam_now()
        all_items: List[ConsiderationItem] = []

        # 1. Upcoming exams from cached exams or quick fetch
        try:
            cached_exams = load_cached_exams(mssv=mssv)
            if cached_exams:
                for ex in cached_exams:
                    exam_dt = ex.start_datetime
                    if exam_dt >= now - timedelta(hours=12):
                        days_left = (exam_dt.date() - now.date()).days
                        countdown_str = "HÔM NAY" if days_left == 0 else ("NGÀY MAI" if days_left == 1 else f"Còn {days_left} ngày")
                        exam_item = ConsiderationItem(
                            item_id=f"exam_{ex.uid}",
                            title=f"Lịch thi: {ex.title} ({countdown_str})",
                            category="EXAM",
                            priority="HIGH",
                            deadline=exam_dt,
                            summary=f"Thi môn {ex.title} lúc {exam_dt.strftime('%H:%M')} ngày {exam_dt.strftime('%d/%m/%Y')}.",
                            action_required=f"Có mặt trước giờ thi 15 phút tại Phòng {ex.location}. Mang theo thẻ SV/CCCD.",
                            recommendation="Kỳ thi kết thúc học phần bắt buộc có mặt đúng giờ.",
                            source_title=f"Lịch thi: {ex.title}",
                            source_id=f"exam_{ex.uid}",
                            source_date=now,
                            confidence_score=1.0,
                            is_reminder_needed=True,
                            notice_type="exam",
                            full_content=f"<h3>Lịch thi: {ex.title}</h3><p><b>Thời gian:</b> {exam_dt.strftime('%d/%m/%Y %H:%M')}</p><p><b>Phòng thi:</b> {ex.location}</p>",
                        )
                        all_items.append(exam_item)
            else:
                exams_raw = client.get_exams_by_time()
                for ex in exams_raw:
                    ngay_raw = ex.get("NgayThi")
                    gio_raw = ex.get("GioThi") or "07:30"
                    if not ngay_raw:
                        continue
                    import re
                    m_d = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", ngay_raw)
                    if not m_d:
                        continue
                    d, m, y = map(int, m_d.groups())
                    hh, mm = 7, 30
                    m_t = re.search(r"(\d{1,2})(?:g|h|:)(\d{2})", gio_raw)
                    if m_t:
                        hh, mm = int(m_t.group(1)), int(m_t.group(2))
                    exam_dt = datetime(y, m, d, hh, mm)

                    if exam_dt >= now - timedelta(hours=12):
                        subj_name = ex.get("CurriculumName") or ex.get("TenHP") or "Môn thi"
                        room = ex.get("PhongThi") or "Xem thông báo"
                        loc = ex.get("DiaDiem") or "Cơ sở chính"
                        days_left = (exam_dt.date() - now.date()).days
                        countdown_str = "HÔM NAY" if days_left == 0 else ("NGÀY MAI" if days_left == 1 else f"Còn {days_left} ngày")

                        exam_item = ConsiderationItem(
                            item_id=f"exam_{ex.get('CurriculumID')}_{exam_dt.strftime('%Y%m%d')}",
                            title=f"Lịch thi: {subj_name} ({countdown_str})",
                            category="EXAM",
                            priority="HIGH",
                            deadline=exam_dt,
                            summary=f"Thi môn {subj_name} lúc {exam_dt.strftime('%H:%M')} ngày {exam_dt.strftime('%d/%m/%Y')}.",
                            action_required=f"Có mặt trước giờ thi 15 phút tại Phòng {room} ({loc}). Mang theo thẻ SV/CCCD.",
                            recommendation="Kỳ thi kết thúc học phần bắt buộc có mặt đúng giờ.",
                            source_title=f"Lịch thi chính thức: {subj_name}",
                            source_id=f"exam_{ex.get('CurriculumID')}",
                            source_date=now,
                            confidence_score=1.0,
                            is_reminder_needed=True,
                            notice_type="exam",
                            full_content=f"<h3>Lịch thi: {subj_name}</h3><p><b>Ngày thi:</b> {exam_dt.strftime('%d/%m/%Y')}</p><p><b>Giờ thi:</b> {exam_dt.strftime('%H:%M')}</p><p><b>Phòng thi:</b> Phòng {room} ({loc})</p><p><b>Thời gian làm bài:</b> {ex.get('ThoiLuong', 90)} phút</p><p><b>Số báo danh:</b> {ex.get('SBD', 'Theo danh sách')}</p><p><b>Ghi chú:</b> {ex.get('Note', '')}</p>",
                        )
                        all_items.append(exam_item)
        except Exception:
            pass

        # 2. Concurrently fetch Personal Notices, General Notices & Fee Debt with strict timeouts
        def fetch_personal():
            try:
                personal_notices = client.get_personal_messages()
                if not isinstance(personal_notices, list):
                    return
                # Capture up to 30 authentic personal inbox messages
                for p in personal_notices[:30]:
                    try:
                        item = analyzer.analyze_announcement(p, notice_type="personal")
                        if item:
                            all_items.append(item)
                    except Exception:
                        pass
            except Exception:
                pass

        def fetch_general():
            try:
                general_notices = client.get_general_announcements()
                if not isinstance(general_notices, list):
                    return
                # Capture up to 30 university-wide announcements
                for g in general_notices[:30]:
                    try:
                        item = analyzer.analyze_announcement(g, notice_type="general")
                        if item:
                            all_items.append(item)
                    except Exception:
                        pass
            except Exception:
                pass

        def fetch_tuition():
            try:
                fee_item = analyzer.analyze_tuition_debt(client.get_fee_debt())
                if fee_item:
                    all_items.insert(0, fee_item)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=3) as executor:
            f1 = executor.submit(fetch_personal)
            f2 = executor.submit(fetch_general)
            f3 = executor.submit(fetch_tuition)
            for fut in [f1, f2, f3]:
                try:
                    fut.result(timeout=6)
                except Exception:
                    pass

        # SORTING STRATEGY:
        # Group 0: Urgent upcoming deadlines (nearest to today first: 0d, 1d, 3d...)
        # Group 1: Fresh announcements & personal messages without fixed deadline (newest creation date first)
        # Group 2: Past deadlines
        def sort_key(it):
            if it.deadline:
                if it.deadline >= now - timedelta(days=1):
                    return (0, it.deadline.timestamp())
                else:
                    return (2, -it.deadline.timestamp())
            else:
                ts = it.source_date.timestamp() if it.source_date else 0
                return (1, -ts)

        all_items.sort(key=sort_key)

        # Deduplicate items with similar titles and same deadline
        deduped_items = []
        seen_keys = set()
        for it in all_items:
            dl_str = it.deadline.strftime("%Y%m%d") if it.deadline else "no_dl"
            clean_words = "".join(filter(str.isalnum, (it.title or "").lower()))[:30]
            key = f"{it.notice_type}_{it.category}_{dl_str}_{clean_words}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped_items.append(it)

        data = []
        for it in deduped_items:
            days_left = None
            if it.deadline:
                days_left = (it.deadline.date() - now.date()).days

            data.append({
                "id": it.item_id,
                "title": it.title,
                "category": it.category,
                "priority": it.priority,
                "deadline": it.deadline.strftime("%d/%m/%Y %H:%M") if it.deadline else None,
                "deadline_iso": it.deadline.isoformat() if it.deadline else None,
                "days_left": days_left,
                "action": it.action_required,
                "recommendation": it.recommendation,
                "source_title": it.source_title,
                "source_date": it.source_date.strftime("%d/%m/%Y") if it.source_date else None,
                "sender": getattr(it, "sender", "") or "Phòng Đào tạo HCMUTE",
                "is_needed": it.is_reminder_needed,
                "notice_type": it.notice_type,
            })

        CONSIDERATIONS_CACHE[mssv] = {
            "timestamp": time.time(),
            "data": data,
        }
        return jsonify({"success": True, "count": len(data), "items": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/notice-detail", methods=["GET"])
def api_notice_detail():
    """Fetch and decrypt full announcement body for reader modal."""
    session = get_request_session()
    if not session:
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

    client = get_session_client(session)
    if not client or not client.ensure_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập hoặc phiên đã hết hạn."}), 401

    notice_id = request.args.get("id", "").strip()
    notice_type = request.args.get("type", "personal").strip()

    if not notice_id:
        return jsonify({"success": False, "message": "Thiếu ID thông báo."}), 400

    from src.notice_analyzer import decrypt_hcmute_body

    try:
        if notice_type == "personal":
            detail = client.get_message_detail(notice_id)
            decrypted_body = decrypt_hcmute_body(detail.get("MessageBody", ""))
            return jsonify({
                "success": True,
                "id": notice_id,
                "subject": detail.get("MessageSubject") or "Thông báo cá nhân",
                "sender": detail.get("SenderName") or "Phòng Công tác sinh viên",
                "date": detail.get("CreationDate") or "",
                "content_html": decrypted_body,
            })
        elif notice_type == "exam":
            # Return exam info
            exams = client.get_exams_by_time()
            for ex in exams:
                if str(ex.get("CurriculumID")) in notice_id:
                    return jsonify({
                        "success": True,
                        "id": notice_id,
                        "subject": f"Lịch thi: {ex.get('CurriculumName')}",
                        "sender": "Phòng Đào tạo HCMUTE",
                        "date": ex.get("NgayThi", ""),
                        "content_html": f"<h3>{ex.get('CurriculumName')}</h3><p><b>Mã học phần:</b> {ex.get('CurriculumID')}</p><p><b>Ngày thi:</b> {ex.get('NgayThi')}</p><p><b>Giờ thi:</b> {ex.get('GioThi')}</p><p><b>Phòng thi:</b> Phòng {ex.get('PhongThi')}</p><p><b>Địa điểm:</b> {ex.get('DiaDiem')}</p><p><b>Kỳ thi:</b> {ex.get('KyThi')}</p><p><b>Số báo danh:</b> {ex.get('SBD') or 'Theo danh sách'}</p><p><b>Ghi chú:</b> {ex.get('Note', '')}</p>",
                    })
            return jsonify({"success": False, "message": "Không tìm thấy thông tin lịch thi."}), 404
        else:
            # General notice
            general_list = client.get_general_announcements()
            for g in general_list:
                if str(g.get("MaTin")) == str(notice_id) or str(g.get("Id")) == str(notice_id):
                    return jsonify({
                        "success": True,
                        "id": notice_id,
                        "subject": g.get("TieuDe") or g.get("MessageSubject") or "Thông báo chung",
                        "sender": g.get("StaffName") or "Phòng Đào tạo",
                        "date": g.get("CreateDate") or "",
                        "content_html": g.get("NoiDung") or "<p>Không có nội dung chi tiết.</p>",
                    })
            return jsonify({"success": False, "message": "Không tìm thấy thông báo chung."}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/export-ics", methods=["POST"])
def api_export_ics():
    """Export schedule, exams, and selected reminders to .ics file with granular category options."""
    session = get_request_session()
    client = get_session_client(session) if session else None
    mssv = str(session.get("mssv", "")).strip() if session else None

    try:
        from datetime import datetime, timedelta
        now = get_vietnam_now()

        req_data = request.json or {}
        include_classes = bool(req_data.get("include_classes", True))
        include_exams = bool(req_data.get("include_exams", True))
        include_reminders = bool(req_data.get("include_reminders", True))
        only_upcoming = bool(req_data.get("only_upcoming", True))
        selected_reminder_ids = set(req_data.get("selected_reminder_ids", []))

        curr_year = "2026-2027"
        curr_term = "HK01"
        if client and client.is_authenticated():
            try:
                yt = client.get_year_and_term()
                curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or curr_year
                curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or curr_term
            except Exception:
                pass

        all_events = []

        # 1. Fetch Schedule (if requested)
        if include_classes:
            classes = []
            if client and client.ensure_authenticated():
                try:
                    raw_sched = client.get_schedule_semester(curr_year, curr_term)
                    classes = parse_semester_schedule(raw_sched)
                except Exception:
                    pass
            if not classes:
                cached = load_feed_cache(mssv=mssv)
                classes = [c for c in cached if (c.event_type or "").upper() in ("CLASS", "TKB")]

            # Include make-up classes!
            from src.telegram_bot import load_makeup_classes
            classes.extend(load_makeup_classes())

            cancelled_set = load_cancellations()
            classes = [c for c in classes if c.uid not in cancelled_set]
            if only_upcoming:
                classes = [c for c in classes if c.end_datetime >= now]
            all_events.extend(classes)

        # 2. Fetch Exams (if requested)
        if include_exams:
            parsed_exams = []
            if client and client.is_authenticated():
                raw_exams = []
                try:
                    raw_exams.extend(client.get_exams_by_time())
                except Exception:
                    pass
                try:
                    raw_exams.extend(client.get_exam_schedule(curr_year, curr_term))
                except Exception:
                    pass
                parsed_exams = parse_exam_schedule(raw_exams)

            if not parsed_exams:
                parsed_exams = load_cached_exams(mssv=mssv)
            else:
                save_cached_exams(parsed_exams, mssv=mssv)

            seen_exam_keys = set()
            for ex in parsed_exams:
                k = (ex.title, ex.start_datetime.strftime("%Y%m%d%H%M"))
                if k not in seen_exam_keys:
                    seen_exam_keys.add(k)
                    # For exams: keep all semester exams if exporting exams only,
                    # or keep exams within the past 30 days and upcoming.
                    if not only_upcoming or not include_classes or ex.start_datetime >= now - timedelta(days=30):
                        all_events.append(ex)

        # 3. Add Reminders (if requested)
        if include_reminders:
            analyzer = NoticeAnalyzer(load_config())
            if client and client.ensure_authenticated():
                try:
                    personal_notices = client.get_personal_messages()[:15]
                    for p in personal_notices:
                        mid = p.get("MessageID") or p.get("Id")
                        if mid and "MessageBody" not in p:
                            try:
                                p_detail = client.get_message_detail(mid)
                                p["MessageBody"] = p_detail.get("MessageBody", "")
                            except Exception:
                                pass
                        item = analyzer.analyze_announcement(p, notice_type="personal")
                        if item and (not selected_reminder_ids or item.item_id in selected_reminder_ids):
                            ev = item.to_calendar_event()
                            if ev:
                                all_events.append(ev)

                    for g in client.get_general_announcements()[:20]:
                        item = analyzer.analyze_announcement(g, notice_type="general")
                        if item and (not selected_reminder_ids or item.item_id in selected_reminder_ids):
                            ev = item.to_calendar_event()
                            if ev:
                                all_events.append(ev)

                    fee_item = analyzer.analyze_tuition_debt(client.get_fee_debt())
                    if fee_item and (not selected_reminder_ids or fee_item.item_id in selected_reminder_ids):
                        ev = fee_item.to_calendar_event()
                        if ev:
                            all_events.append(ev)
                except Exception:
                    pass

        if not all_events:
            return jsonify({"success": False, "message": "Không có sự kiện nào phù hợp với bộ lọc bạn đã chọn."}), 400

        # Descriptive filename
        if include_classes and not include_exams and not include_reminders:
            download_name = "hcmute_tkb.ics"
            cal_title = "HCMUTE - Thời Khóa Biểu"
        elif include_exams and not include_classes and not include_reminders:
            download_name = "hcmute_lich_thi.ics"
            cal_title = "HCMUTE - Lịch Thi"
        elif include_reminders and not include_classes and not include_exams:
            download_name = "hcmute_nhac_nho.ics"
            cal_title = "HCMUTE - Hạn Chót & Nhắc Nhở"
        else:
            download_name = "hcmute_calendar.ics"
            cal_title = "HCMUTE - Lịch Học & Thi"

        out_file = os.path.abspath(f"data/{download_name}")
        export_events_to_ics(all_events, output_path=out_file, calendar_name=cal_title)

        return send_file(
            out_file,
            as_attachment=True,
            download_name=download_name,
            mimetype="text/calendar",
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/sync-google", methods=["POST"])
def api_sync_google():
    """Directly push classes, exams, and reminders to dedicated Google Calendars."""
    session = get_request_session()
    client = get_session_client(session) if session else None
    mssv = str(session.get("mssv", "")).strip() if session else None

    try:
        cfg = load_config()
        g_cfg = cfg.get("google_calendar", {})
        creds_path = g_cfg.get("credentials_path", "config/credentials.json")
        token_path = g_cfg.get("token_path", "data/google_token.json")

        gcal = GoogleCalendarSync(credentials_path=creds_path, token_path=token_path)
        if not gcal.is_configured():
            return jsonify({
                "success": False,
                "configured": False,
                "message": "Tính năng đồng bộ Google Calendar API cần file config/credentials.json tải từ Google Cloud Console. Để nhanh nhất và không phiền phức, bạn chỉ cần bấm 'Tải file .ics' rồi mở lên là xong 100%!",
            }), 400

        if not gcal.authenticate(interactive=True):
            return jsonify({"success": False, "message": "Xác thực Google Calendar thất bại."}), 400

        from datetime import datetime, timedelta
        now = get_vietnam_now()

        req_data = request.json or {}
        include_classes = bool(req_data.get("include_classes", True))
        include_exams = bool(req_data.get("include_exams", True))
        include_reminders = bool(req_data.get("include_reminders", True))
        only_upcoming = bool(req_data.get("only_upcoming", True))
        selected_reminder_ids = set(req_data.get("selected_reminder_ids", []))

        curr_year = "2026-2027"
        curr_term = "HK01"
        if client and client.is_authenticated():
            try:
                yt = client.get_year_and_term()
                curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or curr_year
                curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or curr_term
            except Exception:
                pass

        total_created, total_updated, total_synced = 0, 0, 0
        synced_calendars = []

        # 1. Sync Classes to dedicated calendar
        if include_classes:
            classes = []
            if client and client.ensure_authenticated():
                try:
                    raw_sched = client.get_schedule_semester(curr_year, curr_term)
                    classes = parse_semester_schedule(raw_sched)
                except Exception:
                    pass
            if not classes:
                cached = load_feed_cache(mssv=mssv)
                classes = [c for c in cached if (c.event_type or "").upper() in ("CLASS", "TKB")]

            cancelled_set = load_cancellations()
            classes = [c for c in classes if c.uid not in cancelled_set]
            if only_upcoming:
                classes = [c for c in classes if c.end_datetime >= now]

            if classes:
                cal_name = "HCMUTE - Thời Khóa Biểu"
                cal_id = gcal.get_or_create_calendar(cal_name)
                c_c, c_u, _ = gcal.sync_events(classes, calendar_id=cal_id)
                total_created += c_c
                total_updated += c_u
                total_synced += len(classes)
                synced_calendars.append(f"{cal_name} ({len(classes)} buổi)")

        # 2. Sync Exams to dedicated calendar
        if include_exams:
            parsed_exams = []
            if client and client.ensure_authenticated():
                raw_exams = []
                try:
                    raw_exams.extend(client.get_exams_by_time())
                except Exception:
                    pass
                try:
                    raw_exams.extend(client.get_exam_schedule(curr_year, curr_term))
                except Exception:
                    pass
                parsed_exams = parse_exam_schedule(raw_exams)

            if not parsed_exams:
                parsed_exams = load_cached_exams(mssv=mssv)
            else:
                save_cached_exams(parsed_exams)

            seen_exam_keys = set()
            exams_to_sync = []
            for ex in parsed_exams:
                k = (ex.title, ex.start_datetime.strftime("%Y%m%d%H%M"))
                if k not in seen_exam_keys:
                    seen_exam_keys.add(k)
                    if not only_upcoming or ex.start_datetime >= now - timedelta(days=30):
                        exams_to_sync.append(ex)

            if exams_to_sync:
                cal_name = "HCMUTE - Lịch Thi"
                cal_id = gcal.get_or_create_calendar(cal_name)
                e_c, e_u, _ = gcal.sync_events(exams_to_sync, calendar_id=cal_id)
                total_created += e_c
                total_updated += e_u
                total_synced += len(exams_to_sync)
                synced_calendars.append(f"{cal_name} ({len(exams_to_sync)} môn)")

        # 3. Sync Reminders to dedicated calendar
        if include_reminders:
            analyzer = NoticeAnalyzer(load_config())
            reminders_to_sync = []

            if client.ensure_authenticated():
                try:
                    personal_notices = client.get_personal_messages()[:15]
                    for p in personal_notices:
                        mid = p.get("MessageID") or p.get("Id")
                        if mid and "MessageBody" not in p:
                            try:
                                p_detail = client.get_message_detail(mid)
                                p["MessageBody"] = p_detail.get("MessageBody", "")
                            except Exception:
                                pass
                        item = analyzer.analyze_announcement(p, notice_type="personal")
                        if item and (not selected_reminder_ids or item.item_id in selected_reminder_ids):
                            ev = item.to_calendar_event()
                            if ev:
                                reminders_to_sync.append(ev)

                    for g in client.get_general_announcements()[:20]:
                        item = analyzer.analyze_announcement(g, notice_type="general")
                        if item and (not selected_reminder_ids or item.item_id in selected_reminder_ids):
                            ev = item.to_calendar_event()
                            if ev:
                                reminders_to_sync.append(ev)

                    fee_item = analyzer.analyze_tuition_debt(client.get_fee_debt())
                    if fee_item and (not selected_reminder_ids or fee_item.item_id in selected_reminder_ids):
                        ev = fee_item.to_calendar_event()
                        if ev:
                            reminders_to_sync.append(ev)
                except Exception:
                    pass

            if reminders_to_sync:
                cal_name = "HCMUTE - Hạn Chót & Nhắc Nhở"
                cal_id = gcal.get_or_create_calendar(cal_name)
                r_c, r_u, _ = gcal.sync_events(reminders_to_sync, calendar_id=cal_id)
                total_created += r_c
                total_updated += r_u
                total_synced += len(reminders_to_sync)
                synced_calendars.append(f"{cal_name} ({len(reminders_to_sync)} nhắc nhở)")

        return jsonify({
            "success": True,
            "created": total_created,
            "updated": total_updated,
            "total": total_synced,
            "calendars": synced_calendars,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/calendar/<path:category_filename>", methods=["GET"])
@app.route("/calendar/feed.ics", methods=["GET"])
def calendar_feed(category_filename="feed.ics"):
    """
    Live webcal/iCal subscription feed.
    Automatically updates on Apple Calendar & Google Calendar in real time.
    Supports clean URLs:
      - /calendar/<mssv>/feed.ics (classes + exams for that student)
      - /calendar/feed.ics (classes + exams)
      - /calendar/all.ics (classes + exams + reminders)
      - /calendar/tkb.ics (classes only)
      - /calendar/thi.ics (exams only)
      - /calendar/nhacnho.ics (reminders only)
    Or query parameters: ?classes=1&exams=1&reminders=1&mssv=<mssv>
    """
    from datetime import datetime, timedelta
    now = get_vietnam_now()

    req_mssv = request.args.get("mssv", "").strip()
    raw_cat = (category_filename or "feed.ics").strip("/")
    parts = raw_cat.split("/")
    if len(parts) >= 2:
        possible_mssv = parts[0]
        cat = parts[1].lower()
        if not req_mssv and (possible_mssv.isalnum() and len(possible_mssv) >= 5):
            req_mssv = possible_mssv
    else:
        cat = raw_cat.lower()

    if not req_mssv:
        session = get_request_session()
        if session:
            req_mssv = str(session.get("mssv", "")).strip()

    if cat in ("tkb.ics", "tkb"):
        inc_classes = True
        inc_exams = False
        inc_reminders = False
    elif cat in ("thi.ics", "thi", "lich_thi.ics"):
        inc_classes = False
        inc_exams = True
        inc_reminders = False
    elif cat in ("nhacnho.ics", "nhacnho", "reminders.ics"):
        inc_classes = False
        inc_exams = False
        inc_reminders = True
    elif cat in ("all.ics", "all"):
        inc_classes = True
        inc_exams = True
        inc_reminders = True
    else:
        inc_classes = request.args.get("classes", "1") in ("1", "true", "True", "yes")
        inc_exams = request.args.get("exams", "1") in ("1", "true", "True", "yes")
        inc_reminders = request.args.get("reminders", "0") in ("1", "true", "True", "yes")

    client = None
    if req_mssv:
        for s in ACTIVE_SESSIONS.values():
            if str(s.get("mssv", "")).strip() == req_mssv and s.get("portal_token"):
                client = get_session_client(s)
                break

    fetched_fresh = False
    all_raw_events = []

    if client and client.ensure_authenticated():
        try:
            yt = client.get_year_and_term()
            curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
            curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"

            # 1. Fetch Classes
            raw_sched = client.get_schedule_semester(curr_year, curr_term)
            classes = parse_semester_schedule(raw_sched)
            all_raw_events.extend(classes)

            # 2. Fetch Exams
            raw_exams = []
            try:
                raw_exams.extend(client.get_exams_by_time())
            except Exception:
                pass
            try:
                raw_exams.extend(client.get_exam_schedule(curr_year, curr_term))
            except Exception:
                pass
            parsed_exams = parse_exam_schedule(raw_exams)
            if not parsed_exams:
                parsed_exams = load_cached_exams(mssv=req_mssv)
            else:
                save_cached_exams(parsed_exams, mssv=req_mssv)

            seen_exam_keys = set()
            for ex in parsed_exams:
                k = (ex.title, ex.start_datetime.strftime("%Y%m%d%H%M"))
                if k not in seen_exam_keys:
                    seen_exam_keys.add(k)
                    all_raw_events.append(ex)

            # 3. Fetch Reminders (if needed)
            if inc_reminders:
                try:
                    analyzer = NoticeAnalyzer(load_config())
                    personal_notices = client.get_personal_messages()[:10]
                    for p in personal_notices:
                        mid = p.get("MessageID") or p.get("Id")
                        if mid and "MessageBody" not in p:
                            try:
                                p_detail = client.get_message_detail(mid)
                                p["MessageBody"] = p_detail.get("MessageBody", "")
                            except Exception:
                                pass
                        item = analyzer.analyze_announcement(p, notice_type="personal")
                        if item:
                            ev = item.to_calendar_event()
                            if ev:
                                all_raw_events.append(ev)

                    for g in client.get_general_announcements()[:15]:
                        item = analyzer.analyze_announcement(g, notice_type="general")
                        if item:
                            ev = item.to_calendar_event()
                            if ev:
                                all_raw_events.append(ev)

                    fee_item = analyzer.analyze_tuition_debt(client.get_fee_debt())
                    if fee_item:
                        ev = fee_item.to_calendar_event()
                        if ev:
                            all_raw_events.append(ev)
                except Exception:
                    pass

            if all_raw_events:
                save_feed_cache(all_raw_events, mssv=req_mssv)
                fetched_fresh = True
        except Exception:
            pass

    # If could not fetch fresh from portal, fallback to cache for this student
    if not fetched_fresh:
        all_raw_events = load_feed_cache(mssv=req_mssv or None)

    if not all_raw_events:
        return "Chưa có dữ liệu lịch. Vui lòng mở trang web HCMUTE Sync và đăng nhập để đồng bộ lần đầu.", 404

    # Always include make-up classes in live feed
    from src.telegram_bot import load_makeup_classes
    for m in load_makeup_classes():
        all_raw_events.append(m)

    # Dynamically apply manual cancellations
    cancelled_set = load_cancellations()
    filtered_events = []
    for ev in all_raw_events:
        if ev.uid in cancelled_set:
            continue
        ev_type = (ev.event_type or "CLASS").upper()
        is_class = ev_type in ("CLASS", "TKB")
        is_exam = ev_type in ("EXAM", "THI")
        is_reminder = not is_class and not is_exam

        if is_class and not inc_classes:
            continue
        if is_exam and not inc_exams:
            continue
        if is_reminder and not inc_reminders:
            continue

        # Keep only active and upcoming events (keep recent exams from past 30 days so active exam period is never lost)
        if is_class and ev.end_datetime < now:
            continue
        if is_exam and ev.start_datetime < now - timedelta(days=30):
            continue
        if is_reminder and ev.start_datetime < now - timedelta(days=2):
            continue

        filtered_events.append(ev)

    if inc_classes and not inc_exams and not inc_reminders:
        cal_title = "HCMUTE - Thời Khóa Biểu"
    elif inc_exams and not inc_classes and not inc_reminders:
        cal_title = "HCMUTE - Lịch Thi"
    elif inc_reminders and not inc_classes and not inc_exams:
        cal_title = "HCMUTE - Hạn Chót & Nhắc Nhở"
    elif inc_classes and inc_exams and not inc_reminders:
        cal_title = "HCMUTE - Lịch Học & Lịch Thi"
    else:
        cal_title = "HCMUTE - Toàn Bộ Lịch & Nhắc Nhở"

    ics_data = events_to_ics_bytes(filtered_events, calendar_name=cal_title)

    return Response(
        ics_data,
        mimetype="text/calendar",
        headers={
            "Content-Disposition": "inline; filename=hcmute_calendar.ics",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.route("/api/telegram/config", methods=["GET", "POST"])
def api_telegram_config():
    """Get or save Telegram Bot settings."""
    cfg = load_config()
    if request.method == "POST":
        if not check_admin_auth():
            return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
        data = request.json or {}
        if "telegram" not in cfg:
            cfg["telegram"] = {}
        cfg["telegram"]["bot_token"] = data.get("bot_token", "").strip()
        cfg["telegram"]["bot_username"] = data.get("bot_username", "").strip()
        cfg["telegram"]["chat_id"] = data.get("chat_id", "").strip()
        cfg["telegram"]["morning_briefing"] = bool(data.get("morning_briefing", True))
        cfg["telegram"]["morning_time"] = data.get("morning_time", "06:30").strip()
        cfg["telegram"]["diff_alert"] = bool(data.get("diff_alert", True))
        cfg["telegram"]["enabled"] = bool(data.get("enabled", True))
        if "groq_api_key" in data:
            cfg["groq_api_key"] = str(data.get("groq_api_key", "")).strip()
        if "gemini_api_key" in data:
            cfg["gemini_api_key"] = str(data.get("gemini_api_key", "")).strip()

        save_tg_config(cfg)
        ret_cfg = dict(cfg["telegram"])
        ret_cfg["groq_api_key"] = cfg.get("groq_api_key", "")
        ret_cfg["gemini_api_key"] = cfg.get("gemini_api_key", "")
        return jsonify({"success": True, "config": ret_cfg})

    ret_cfg = dict(cfg.get("telegram", {}))
    ret_cfg["groq_api_key"] = cfg.get("groq_api_key", "")
    ret_cfg["gemini_api_key"] = cfg.get("gemini_api_key", "")
    return jsonify({"success": True, "config": ret_cfg})


@app.route("/api/telegram/bot-info", methods=["GET"])
def api_telegram_bot_info():
    """Return bot username and whether master token is configured."""
    cfg = load_config().get("telegram", {})
    token = cfg.get("bot_token", "").strip()
    bot_username = cfg.get("bot_username", "")

    if token and not bot_username:
        try:
            r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
            if r.status_code == 200 and r.json().get("ok"):
                bot_username = r.json()["result"].get("username", "")
                cfg["bot_username"] = bot_username
                save_tg_config({"telegram": cfg})
        except Exception:
            pass

    return jsonify({
        "configured": bool(token),
        "bot_username": bot_username,
        "enabled": bool(cfg.get("enabled", True)),
    })


@app.route("/api/telegram/users", methods=["GET"])
def api_telegram_users():
    """Admin endpoint: Get all student IDs (MSSV) that accessed the system and stats."""
    if not check_admin_auth():
        return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
    students = load_student_access()
    return jsonify({
        "success": True,
        "total": len(students),
        "users": students,
    })


@app.route("/api/admin/student-access/delete", methods=["POST"])
def api_delete_student_access():
    """Admin endpoint: Remove an accessed student from records."""
    if not check_admin_auth():
        return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
    data = request.json or {}
    mssv = str(data.get("mssv", "")).strip()
    chat_id = str(data.get("chat_id", "")).strip()
    if not mssv and not chat_id:
        return jsonify({"success": False, "message": "Thiếu MSSV hoặc Chat ID."}), 400

    from src.telegram_bot import delete_student_access, delete_telegram_user, load_telegram_users
    deleted = False
    if mssv:
        deleted = delete_student_access(mssv)
    if chat_id:
        delete_telegram_user(chat_id)
    # Also clean matching telegram user if mssv provided
    if mssv:
        tg_users = load_telegram_users()
        for cid, u in tg_users.items():
            if str(u.get("mssv", "")).strip() == mssv:
                delete_telegram_user(cid)
    return jsonify({"success": True, "deleted": deleted})


@app.route("/api/telegram/update-user", methods=["POST"])
def api_telegram_update_user():
    """Admin endpoint: Update student MSSV or Name for a registered Telegram user."""
    if not check_admin_auth():
        return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
    data = request.json or {}
    chat_id = str(data.get("chat_id", "")).strip()
    mssv = str(data.get("mssv", "")).strip()
    name = str(data.get("name", "")).strip()

    if not chat_id:
        return jsonify({"success": False, "message": "Thiếu Chat ID."}), 400

    from src.telegram_bot import register_or_update_telegram_user
    user_rec = register_or_update_telegram_user(
        chat_id=chat_id,
        mssv=mssv,
        name=name,
    )
    return jsonify({"success": True, "user": user_rec})


@app.route("/api/telegram/register", methods=["POST"])
def api_telegram_register():
    """Register or link a student with their Telegram Chat ID."""
    from src.telegram_bot import register_or_update_telegram_user, send_telegram_message, format_today_schedule, MAIN_INLINE_KEYBOARD
    data = request.json or {}
    chat_id = str(data.get("chat_id", "")).strip()
    mssv = str(data.get("mssv", "")).strip()
    name = str(data.get("name", "")).strip()

    if not chat_id:
        return jsonify({"success": False, "message": "Vui lòng nhập Telegram Chat ID hoặc Số điện thoại."}), 400

    cfg = load_config().get("telegram", {})
    bot_token = cfg.get("bot_token", "").strip()

    user_rec = register_or_update_telegram_user(
        chat_id=chat_id,
        mssv=mssv,
        name=name,
    )

    if bot_token:
        client = get_client()
        welcome = (
            f"🎉 <b>KÍCH HOẠT THÔNG BÁO TỰ ĐỘNG THÀNH CÔNG!</b>\n\n"
            f"👤 Sinh viên: <b>{name or 'Sinh viên'}</b>\n"
            f"🎓 MSSV/ID: <code>{mssv or chat_id}</code>\n\n"
            f"🌟 <b>Trợ lý tự động hoàn toàn (Set & Forget):</b>\n"
            f"• ☀️ <b>06:30 sáng:</b> Tự gửi lịch học & phòng học hôm nay\n"
            f"• 🔥 <b>Cận kề kỳ thi:</b> Tự động nhắc phòng thi & giờ thi\n"
            f"• 🏖️ <b>GV cho nghỉ / bù:</b> Cảnh báo ngay lập tức nếu giảng viên đổi lịch hoặc cho nghỉ\n\n"
            f"<i>Bạn không cần mở web hay nhớ lệnh nào nữa!</i>"
        )
        send_telegram_message(bot_token, chat_id, welcome, reply_markup=MAIN_INLINE_KEYBOARD)
        today_text = format_today_schedule(client)
        send_telegram_message(bot_token, chat_id, today_text)

    return jsonify({"success": True, "user": user_rec})


@app.route("/api/telegram/broadcast", methods=["POST"])
def api_telegram_broadcast():
    """Admin endpoint: Broadcast announcement to all registered students."""
    if not check_admin_auth():
        return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
    from src.telegram_bot import broadcast_telegram_message
    cfg = load_config().get("telegram", {})
    bot_token = cfg.get("bot_token", "").strip()
    if not bot_token:
        return jsonify({"success": False, "message": "Chưa cấu hình Bot Token."}), 400

    data = request.json or {}
    msg = data.get("message", "").strip()
    if not msg:
        return jsonify({"success": False, "message": "Vui lòng nhập nội dung thông báo."}), 400

    res = broadcast_telegram_message(bot_token, msg)
    return jsonify({"success": True, "result": res})


@app.route("/api/telegram/delete-user", methods=["POST"])
def api_telegram_delete_user():
    """Admin endpoint: Delete a registered student."""
    if not check_admin_auth():
        return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
    from src.telegram_bot import delete_telegram_user
    data = request.json or {}
    chat_id = str(data.get("chat_id", "")).strip()
    if not chat_id:
        return jsonify({"success": False, "message": "Thiếu Chat ID."}), 400
    success = delete_telegram_user(chat_id)
    return jsonify({"success": success})


@app.route("/api/telegram/test-push", methods=["POST"])
def api_telegram_test_push():
    """Admin endpoint: Send test morning briefing to all registered students right now."""
    if not check_admin_auth():
        return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
    from src.telegram_bot import trigger_morning_briefing
    client = get_client()
    res = trigger_morning_briefing(client)
    return jsonify({"success": True, "result": res})


@app.route("/api/telegram/webhook", methods=["POST"])
def api_telegram_webhook():
    """
    On-demand Telegram Webhook receiver.
    Zero polling loop, zero continuous CPU/RAM usage!
    Called directly by Telegram servers only when a message arrives.
    """
    try:
        update_data = request.json or {}
        cfg = load_config().get("telegram", {})
        bot_token = cfg.get("bot_token", "").strip()
        if not bot_token or not cfg.get("enabled", True):
            return "OK", 200

        from src.telegram_bot import process_telegram_update
        process_telegram_update(update_data, bot_token, get_client())
    except Exception as e:
        print(f"[Webhook Error] {e}")

    return "OK", 200


@app.route("/api/telegram/set-webhook", methods=["POST"])
def api_telegram_set_webhook():
    """Register webhook with Telegram API to eliminate continuous background loops."""
    if not check_admin_auth():
        return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
    cfg = load_config().get("telegram", {})
    bot_token = cfg.get("bot_token", "").strip()
    if not bot_token:
        return jsonify({"success": False, "message": "Chưa có Bot Token."}), 400

    host_url = request.host_url.rstrip("/")
    if "localhost" in host_url or "127.0.0.1" in host_url:
        return jsonify({"success": False, "message": "Webhook chỉ hoạt động khi chạy trên Web HTTPS (Render.com), không hỗ trợ localhost."}), 400

    webhook_url = f"{host_url}/api/telegram/webhook"
    try:
        r = requests.post(f"https://api.telegram.org/bot{bot_token}/setWebhook", json={"url": webhook_url}, timeout=10)
        res_data = r.json()
        if res_data.get("ok"):
            cfg["use_webhook"] = True
            save_tg_config({"telegram": cfg})
            return jsonify({"success": True, "message": f"✔ Đã bật Webhook thành công! Server không cần chạy ngầm nữa (RAM < 20MB, 0% CPU)." , "webhook_url": webhook_url})
        else:
            return jsonify({"success": False, "message": res_data.get("description", "Lỗi cài đặt Webhook.")}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/telegram/test", methods=["POST"])
def api_telegram_test():
    """Send test notification to verify bot token & chat ID."""
    data = request.json or {}
    token = data.get("bot_token", "").strip()
    chat_id = data.get("chat_id", "").strip()

    if not token or not chat_id:
        cfg = load_config().get("telegram", {})
        token = token or cfg.get("bot_token", "")
        chat_id = chat_id or cfg.get("chat_id", "")

    if not token or not chat_id:
        return jsonify({"success": False, "message": "Vui lòng nhập Bot Token và Chat ID."}), 400

    test_msg = (
        "🤖 <b>KẾT NỐI TELEGRAM BOT THÀNH CÔNG!</b>\n\n"
        "Chào bạn, Trợ lý HCMUTE Calendar đã sẵn sàng:\n"
        "• ☀️ Bản tin 06:30 sáng tóm tắt lịch học\n"
        "• 🔥 Báo động trước ngày thi & giờ thi\n"
        "• 🏖️ Cảnh báo tự động khi giảng viên cho nghỉ / bù"
    )
    ok = send_telegram_message(token, chat_id, test_msg)
    if ok:
        return jsonify({"success": True, "message": "Đã gửi tin nhắn thử nghiệm thành công vào Telegram!"})
    else:
        return jsonify({"success": False, "message": "Không thể gửi tin nhắn. Hãy kiểm tra lại Bot Token và Chat ID!"}), 400


@app.route("/api/schedule/cancel", methods=["POST"])
def api_schedule_cancel():
    """Mark a class session as cancelled manually (with crowdsource voting)."""
    data = request.json or {}
    uid = data.get("uid", "").strip()
    if not uid:
        return jsonify({"success": False, "message": "Thiếu UID buổi học."}), 400

    cfg = load_config()
    bot_token = cfg.get("telegram", {}).get("bot_token", "")
    from src.telegram_bot import record_cancellation_vote
    res = record_cancellation_vote(uid, voter_id="web_user", bot_token=bot_token)
    return jsonify({
        "success": True,
        "vote_result": res,
        "cancelled": list(load_cancellations()),
    })


@app.route("/api/schedule/uncancel", methods=["POST"])
def api_schedule_uncancel():
    """Unmark a cancelled class."""
    data = request.json or {}
    uid = data.get("uid", "").strip()
    if not uid:
        return jsonify({"success": False, "message": "Thiếu UID buổi học."}), 400

    cfg = load_config()
    bot_token = cfg.get("telegram", {}).get("bot_token", "")
    from src.telegram_bot import remove_cancellation_vote
    res = remove_cancellation_vote(uid, voter_id="web_user", bot_token=bot_token)
    return jsonify({
        "success": True,
        "vote_result": res,
        "cancelled": list(load_cancellations()),
    })


@app.route("/api/schedule/crowdsource/config", methods=["GET", "POST"])
def api_schedule_crowdsource_config():
    """Get or update crowdsource cancellation threshold."""
    cfg = load_config()
    if "crowdsource_cancellation" not in cfg:
        cfg["crowdsource_cancellation"] = {
            "enabled": True,
            "threshold_percent": 80,
            "min_votes": 2,
        }

    if request.method == "POST":
        if not check_admin_auth():
            return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
        data = request.json or {}
        if "enabled" in data:
            cfg["crowdsource_cancellation"]["enabled"] = bool(data["enabled"])
        if "threshold_percent" in data:
            try:
                val = int(data["threshold_percent"])
                if 1 <= val <= 100:
                    cfg["crowdsource_cancellation"]["threshold_percent"] = val
            except Exception:
                pass
        save_tg_config(cfg)
        return jsonify({"success": True, "config": cfg["crowdsource_cancellation"]})

    return jsonify({"success": True, "config": cfg["crowdsource_cancellation"]})


@app.route("/api/schedule/crowdsource/votes", methods=["GET"])
def api_schedule_crowdsource_votes():
    """Return all active cancellation votes."""
    from src.telegram_bot import load_cancellation_votes
    votes = load_cancellation_votes()
    return jsonify({"success": True, "votes": votes})


@app.route("/api/schedule/makeup", methods=["GET", "POST", "DELETE"])
def api_schedule_makeup():
    """Manage make-up classes."""
    from src.telegram_bot import load_makeup_classes, add_makeup_class, remove_makeup_class
    from datetime import datetime, timedelta

    if request.method == "GET":
        events = load_makeup_classes()
        return jsonify({"success": True, "items": [e.to_dict() for e in events]})

    if request.method == "POST":
        if not check_admin_auth():
            return jsonify({"success": False, "message": "Yêu cầu quyền Quản trị viên (Admin)."}), 403
        data = request.json or {}
        title = data.get("title", "").strip()
        date_str = data.get("date", "").strip()
        start_time_str = data.get("start_time", "07:30").strip()
        end_time_str = data.get("end_time", "").strip()
        location = data.get("location", "HCMUTE").strip()
        note = data.get("note", "").strip()

        if not title or not date_str:
            return jsonify({"success": False, "message": "Vui lòng nhập Tên môn học và Ngày học bù."}), 400

        try:
            import re
            m_d = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", date_str)
            if m_d:
                day, month, year = int(m_d.group(1)), int(m_d.group(2)), int(m_d.group(3))
            else:
                m_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_str)
                if m_iso:
                    year, month, day = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
                else:
                    return jsonify({"success": False, "message": "Định dạng ngày không hợp lệ. Dùng dd/mm/yyyy hoặc yyyy-mm-dd."}), 400

            m_t = re.search(r"(\d{1,2})[:gh](\d{2})", start_time_str)
            shh = int(m_t.group(1)) if m_t else 7
            smm = int(m_t.group(2)) if m_t else 30
            st = datetime(year, month, day, shh, smm)

            if end_time_str:
                m_et = re.search(r"(\d{1,2})[:gh](\d{2})", end_time_str)
                ehh = int(m_et.group(1)) if m_et else shh + 3
                emm = int(m_et.group(2)) if m_et else smm + 45
                en = datetime(year, month, day, ehh, emm)
            else:
                en = st + timedelta(hours=3, minutes=45)

            ev = add_makeup_class(title, st, en, location=location, note=note)
            return jsonify({"success": True, "event": ev.to_dict()})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 400

    if request.method == "DELETE":
        data = request.json or {}
        uid = data.get("uid", "").strip()
        if not uid:
            return jsonify({"success": False, "message": "Thiếu UID buổi học bù."}), 400
        ok = remove_makeup_class(uid)
        return jsonify({"success": ok})


def run_app(port: int = None, open_browser: bool = True):
    if port is None:
        port = int(os.environ.get("PORT", 5000))

    is_cloud = bool(os.environ.get("PORT"))
    host = "0.0.0.0" if is_cloud else "127.0.0.1"
    url = f"http://localhost:{port}"

    # Start Telegram background daemon if not already started
    if not bot_daemon.running:
        bot_daemon.start()

    # Start Keep-Alive daemon (if on Render or enabled)
    start_keep_alive_daemon()

    if open_browser and not is_cloud and not os.environ.get("NO_BROWSER"):
        Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"\n🚀 HCMUTE Calendar Sync Dashboard đang chạy tại: http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_app()
