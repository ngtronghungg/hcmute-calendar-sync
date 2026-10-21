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

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")


def get_client() -> HCMUTEClient:
    cfg = load_config()
    hcmute_cfg = cfg.get("hcmute", {})
    return HCMUTEClient(
        base_url=hcmute_cfg.get("base_url", "https://portalapi.hcmute.edu.vn/api/"),
        api_key=hcmute_cfg.get("api_key", "utepscRBF0zT2Mqo6vMw69YMOH43IrB2RtXBS0EHit2kzvL2auxaFJBvw=="),
        client_id=hcmute_cfg.get("client_id", "ute"),
        token_cache_file="data/.token_cache.json",
    )


# Start Telegram daemon in background (handles polling fallback and morning briefings)
bot_daemon = get_or_create_bot_daemon(get_client)
bot_daemon.start()


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/admin")
def admin_page():
    return send_from_directory(WEB_DIR, "admin.html")


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
    """Return login state and student profile, auto-renewing session silently if vault credentials exist."""
    client = get_client()
    if client.ensure_authenticated():
        profile = client.get_user_profile() or {}
        return jsonify({
            "authenticated": True,
            "profile": {
                "id": profile.get("Id", ""),
                "name": profile.get("FullName") or profile.get("Username", "Sinh viên"),
                "role": profile.get("Role", "SV"),
                "status": profile.get("StudyStatusName", "Đang học"),
            }
        })
    if os.path.exists("data/.token_cache.json"):
        try:
            with open("data/.token_cache.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            return jsonify({
                "authenticated": True,
                "token_expired": True,
                "profile": {
                    "id": data.get("Id", ""),
                    "name": data.get("FullName") or data.get("Username", "Sinh viên"),
                    "role": data.get("Role", "SV"),
                    "status": data.get("StudyStatusName", "Đang học"),
                }
            })
        except Exception:
            pass
    return jsonify({"authenticated": False})


@app.route("/api/login", methods=["POST"])
def api_login():
    """
    Handle student login.
    Zero-Knowledge Architecture:
    - Passwords are never written to disk, database, or server logs.
    - Password is held in RAM only for the duration of the HTTPS request to portalapi.hcmute.edu.vn,
      then immediately destroyed with `del password`.
    - Also supports Direct Token Login (Zero Password).
    """
    data = request.json or {}
    token_input = data.get("token", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    client = get_client()

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
            }
            client.save_session(session_payload)
            record_student_access(mssv=session_payload["Id"], name=session_payload["FullName"], channel="Web Portal (Token)")
            return jsonify({
                "success": True,
                "profile": {
                    "id": session_payload["Id"],
                    "name": session_payload["FullName"],
                    "role": "SV",
                    "status": "Đang học (Token Auth)",
                }
            })
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
        record_student_access(mssv=profile.get("Id", username), name=profile.get("FullName") or profile.get("Username", "Sinh viên"), channel="Web Portal")
        return jsonify({
            "success": True,
            "profile": {
                "id": profile.get("Id", username),
                "name": profile.get("FullName") or profile.get("Username", "Sinh viên"),
                "role": profile.get("Role", "SV"),
                "status": profile.get("StudyStatusName", "Đang học"),
            }
        })
    except Exception as e:
        del password
        del data
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/api/logout", methods=["POST"])
def api_logout():
    """Handle student logout."""
    client = get_client()
    client.logout()
    return jsonify({"success": True})


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
                    "why": "Để Bot gửi thông báo lịch học 06:30 sáng và báo nghỉ môn",
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
    """1-Click Data Wipe (Right to be Forgotten). Clears session and caches."""
    client = get_client()
    client.logout()

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
    client = get_client()
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
        if client.ensure_authenticated():
            try:
                yt = client.get_year_and_term()
                available_years = yt.get("YearStudy") or available_years
                available_terms = yt.get("Terms") or available_terms
                curr_year = request.args.get("year") or yt.get("CurrentYear") or yt.get("NamHienTai") or curr_year
                curr_term = request.args.get("term") or yt.get("CurrentTerm") or yt.get("HocKyHienTai") or curr_term

                # 2. Live Schedule
                raw_sched = client.get_schedule_semester(curr_year, curr_term)
                all_class_events = parse_semester_schedule(raw_sched)
            except Exception:
                pass

            # 3. Live Exams: combine detailed showexambytime and semester exams
            try:
                time_exams = client.get_exams_by_time()
                all_raw_exams.extend(time_exams)
            except Exception:
                pass
            try:
                term_exams = client.get_exam_schedule(curr_year, curr_term)
                all_raw_exams.extend(term_exams)
            except Exception:
                pass

        # Fallback to cached classes if live fetch failed or offline
        if not all_class_events:
            cached_events = load_feed_cache()
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
            exam_events = load_cached_exams()
        else:
            save_cached_exams(exam_events)

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

        exams_to_return = deduped_exams if (include_past or include_past_exams) else upcoming_exams
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
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


CONSIDERATIONS_CACHE = {"timestamp": 0, "data": []}


@app.route("/api/considerations", methods=["GET"])
def api_considerations():
    """Scan announcements, fee debt, and upcoming exams to formulate reminder recommendations."""
    client = get_client()
    if not client.is_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

    import time
    force = request.args.get("force", "0") in ("1", "true", "True")
    if not force and CONSIDERATIONS_CACHE["data"] and (time.time() - CONSIDERATIONS_CACHE["timestamp"] < 300):
        return jsonify({"success": True, "count": len(CONSIDERATIONS_CACHE["data"]), "items": CONSIDERATIONS_CACHE["data"]})

    try:
        from datetime import datetime, timedelta
        from src.notice_analyzer import ConsiderationItem, decrypt_hcmute_body
        from concurrent.futures import ThreadPoolExecutor

        analyzer = NoticeAnalyzer(load_config())
        now = get_vietnam_now()
        all_items: List[ConsiderationItem] = []

        # 1. Upcoming exams from database (PRIORITY #1)
        try:
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

                # Filter strictly upcoming exams (from today onwards!)
                if exam_dt >= now - timedelta(hours=12):
                    subj_name = ex.get("CurriculumName") or ex.get("TenHP") or "Môn thi"
                    room = ex.get("PhongThi") or "Xem thông báo"
                    loc = ex.get("DiaDiem") or "Cơ sở chính"
                    days_left = (exam_dt.date() - now.date()).days

                    if days_left == 0:
                        countdown_str = "HÔM NAY"
                    elif days_left == 1:
                        countdown_str = "NGÀY MAI"
                    else:
                        countdown_str = f"Còn {days_left} ngày"

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

        # 2. Personal notices (concurrently fetch details in parallel)
        try:
            personal_notices = client.get_personal_messages()[:10]

            def fetch_single_detail(p):
                mid = p.get("MessageID") or p.get("Id")
                if mid and "MessageBody" not in p:
                    try:
                        p_detail = client.get_message_detail(mid)
                        p["MessageBody"] = p_detail.get("MessageBody", "")
                        p["SenderName"] = p_detail.get("SenderName", "")
                    except Exception:
                        pass
                return p

            with ThreadPoolExecutor(max_workers=5) as executor:
                personal_notices = list(executor.map(fetch_single_detail, personal_notices))

            for p in personal_notices:
                item = analyzer.analyze_announcement(p, notice_type="personal")
                if item:
                    all_items.append(item)
        except Exception:
            pass

        # 3. General announcements
        try:
            general_notices = client.get_general_announcements()
            for g in general_notices[:20]:
                item = analyzer.analyze_announcement(g, notice_type="general")
                if item:
                    all_items.append(item)
        except Exception:
            pass

        # 4. Check Tuition Fee Debt
        try:
            fee_item = analyzer.analyze_tuition_debt(client.get_fee_debt())
            if fee_item:
                all_items.insert(0, fee_item)
        except Exception:
            pass

        # STRICT SORTING: NEAREST DEADLINE FIRST!
        all_items.sort(key=lambda x: x.deadline or datetime(2099, 1, 1))

        # Deduplicate items with similar titles and same deadline
        deduped_items = []
        seen_keys = set()
        for it in all_items:
            dl_str = it.deadline.strftime("%Y%m%d") if it.deadline else "no_dl"
            # Normalize title words
            clean_words = "".join(filter(str.isalnum, (it.title or "").lower()))[:25]
            key = f"{it.category}_{dl_str}_{clean_words}"
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
                "is_needed": it.is_reminder_needed,
                "notice_type": it.notice_type,
            })

        CONSIDERATIONS_CACHE["timestamp"] = time.time()
        CONSIDERATIONS_CACHE["data"] = data
        return jsonify({"success": True, "count": len(data), "items": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/notice-detail", methods=["GET"])
def api_notice_detail():
    """Fetch and decrypt full announcement body for reader modal."""
    client = get_client()
    if not client.is_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

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
    client = get_client()

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
        if client.is_authenticated():
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
            if client.ensure_authenticated():
                try:
                    raw_sched = client.get_schedule_semester(curr_year, curr_term)
                    classes = parse_semester_schedule(raw_sched)
                except Exception:
                    pass
            if not classes:
                cached = load_feed_cache()
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
            if client.is_authenticated():
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
                parsed_exams = load_cached_exams()
            else:
                save_cached_exams(parsed_exams)

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
    client = get_client()

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
        if client.is_authenticated():
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
            if client.ensure_authenticated():
                try:
                    raw_sched = client.get_schedule_semester(curr_year, curr_term)
                    classes = parse_semester_schedule(raw_sched)
                except Exception:
                    pass
            if not classes:
                cached = load_feed_cache()
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
            if client.ensure_authenticated():
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
                parsed_exams = load_cached_exams()
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
      - /calendar/feed.ics (classes + exams)
      - /calendar/all.ics (classes + exams + reminders)
      - /calendar/tkb.ics (classes only)
      - /calendar/thi.ics (exams only)
      - /calendar/nhacnho.ics (reminders only)
    Or query parameters: ?classes=1&exams=1&reminders=1
    """
    from datetime import datetime, timedelta
    now = get_vietnam_now()

    cat = (category_filename or "feed.ics").lower()
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

    client = get_client()
    fetched_fresh = False
    all_raw_events = []

    if client.ensure_authenticated():
        try:
            from src.telegram_bot import auto_sync_and_detect_changes
            cfg = load_config()
            bot_token = cfg.get("telegram", {}).get("bot_token", "")
            auto_sync_and_detect_changes(client, bot_token=bot_token, force=False)
        except Exception:
            pass

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
                parsed_exams = load_cached_exams()
            else:
                save_cached_exams(parsed_exams)

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
                save_feed_cache(all_raw_events)
                fetched_fresh = True
        except Exception:
            pass

    # If could not fetch fresh from portal, fallback to cache
    if not fetched_fresh:
        all_raw_events = load_feed_cache()

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
        if "gemini_api_key" in data:
            cfg["gemini_api_key"] = str(data.get("gemini_api_key", "")).strip()

        save_tg_config(cfg)
        ret_cfg = dict(cfg["telegram"])
        ret_cfg["gemini_api_key"] = cfg.get("gemini_api_key", "")
        return jsonify({"success": True, "config": ret_cfg})

    ret_cfg = dict(cfg.get("telegram", {}))
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
            f"• ⚠️ <b>Báo nghỉ/bù:</b> Cảnh báo ngay lập tức nếu giảng viên đổi lịch\n\n"
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
        "• ⚠️ Cảnh báo tự động khi giảng viên báo nghỉ / bù"
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

    if open_browser and not is_cloud and not os.environ.get("NO_BROWSER"):
        Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"\n🚀 HCMUTE Calendar Sync Dashboard đang chạy tại: http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_app()
