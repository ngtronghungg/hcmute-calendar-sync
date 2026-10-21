"""
HCMUTE Calendar & Reminder Sync - Local Web Server
Provides modern REST API and serves the Apple/Nothing-style Web UI.
"""

import os
import sys
import json
import webbrowser
from threading import Timer

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
    send_telegram_message,
    load_cancellations,
    add_cancellation,
    save_config as save_tg_config,
)

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "config.json"))

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_client() -> HCMUTEClient:
    cfg = load_config()
    hcmute_cfg = cfg.get("hcmute", {})
    return HCMUTEClient(
        base_url=hcmute_cfg.get("base_url", "https://portalapi.hcmute.edu.vn/api/"),
        api_key=hcmute_cfg.get("api_key", "utepscRBF0zT2Mqo6vMw69YMOH43IrB2RtXBS0EHit2kzvL2auxaFJBvw=="),
        client_id=hcmute_cfg.get("client_id", "ute"),
        token_cache_file="data/.token_cache.json",
    )


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/status", methods=["GET"])
def api_status():
    """Return login state and student profile."""
    client = get_client()
    if client.is_authenticated():
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
    return jsonify({"authenticated": False})


@app.route("/api/login", methods=["POST"])
def api_login():
    """Handle student login."""
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"success": False, "message": "Vui lòng nhập MSSV và Mật khẩu."}), 400

    client = get_client()
    try:
        profile = client.login(username, password)
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
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/api/logout", methods=["POST"])
def api_logout():
    """Handle student logout."""
    client = get_client()
    client.logout()
    return jsonify({"success": True})


@app.route("/api/overview", methods=["GET"])
def api_overview():
    """Fetch current year & term, class schedule, and exam schedule."""
    client = get_client()
    if not client.is_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

    try:
        from datetime import datetime, timedelta
        now = datetime.now()

        # 1. Year & Term
        yt = client.get_year_and_term()
        available_years = yt.get("YearStudy") or ["2026-2027", "2025-2026", "2024-2025"]
        available_terms = yt.get("Terms") or [
            {"TermID": "HK01", "TermName": "Học kỳ 1"},
            {"TermID": "HK02", "TermName": "Học kỳ 2"},
            {"TermID": "HK03", "TermName": "Học kỳ 3"}
        ]

        curr_year = request.args.get("year") or yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
        curr_term = request.args.get("term") or yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"
        include_past = request.args.get("include_past", "false").lower() == "true"

        # 2. Schedule
        raw_sched = client.get_schedule_semester(curr_year, curr_term)
        all_class_events = parse_semester_schedule(raw_sched)

        upcoming_classes = [c for c in all_class_events if c.end_datetime >= now]
        upcoming_classes.sort(key=lambda x: x.start_datetime)

        past_classes = [c for c in all_class_events if c.end_datetime < now]
        past_classes.sort(key=lambda x: x.start_datetime, reverse=True)

        # 3. Exams: combine detailed showexambytime and semester exams
        all_raw_exams = []
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

        exam_events = parse_exam_schedule(all_raw_exams)

        # Deduplicate exams by title and date
        deduped_exams = []
        seen_exam_keys = set()
        for ex in exam_events:
            k = (ex.title, ex.start_datetime.strftime("%Y%m%d%H%M"))
            if k not in seen_exam_keys:
                seen_exam_keys.add(k)
                deduped_exams.append(ex)

        upcoming_exams = [e for e in deduped_exams if e.start_datetime >= now - timedelta(hours=4)]
        upcoming_exams.sort(key=lambda x: x.start_datetime)

        past_exams = [e for e in deduped_exams if e.start_datetime < now - timedelta(hours=4)]
        past_exams.sort(key=lambda x: x.start_datetime, reverse=True)

        # Decide what to return: by default, only upcoming events!
        classes_to_return = all_class_events if include_past else upcoming_classes
        classes_to_return.sort(key=lambda x: x.start_datetime)

        cancelled_set = load_cancellations()
        classes_data = []
        for c in classes_to_return:
            d = c.to_dict()
            d["is_cancelled"] = c.uid in cancelled_set
            classes_data.append(d)

        exams_to_return = deduped_exams if include_past else upcoming_exams
        exams_to_return.sort(key=lambda x: x.start_datetime)

        return jsonify({
            "success": True,
            "year": curr_year,
            "term": curr_term,
            "years": available_years,
            "terms": available_terms,
            "include_past": include_past,
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


@app.route("/api/considerations", methods=["GET"])
def api_considerations():
    """Scan announcements, fee debt, and upcoming exams to formulate reminder recommendations."""
    client = get_client()
    if not client.is_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

    try:
        from datetime import datetime, timedelta
        from src.notice_analyzer import ConsiderationItem, decrypt_hcmute_body

        analyzer = NoticeAnalyzer(load_config())
        now = datetime.now()
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

        # 2. Personal notices (decrypt full bodies of recent notices)
        try:
            personal_notices = client.get_personal_messages()
            # Scan top 15 most recent personal notices
            for p in personal_notices[:15]:
                mid = p.get("MessageID") or p.get("Id")
                if not mid:
                    continue
                try:
                    p_detail = client.get_message_detail(mid)
                    p["MessageBody"] = p_detail.get("MessageBody", "")
                    p["SenderName"] = p_detail.get("SenderName", "")
                except Exception:
                    pass
                item = analyzer.analyze_announcement(p, notice_type="personal")
                if item:
                    all_items.append(item)
        except Exception:
            pass

        # 3. General announcements
        try:
            general_notices = client.get_general_announcements()
            for g in general_notices[:25]:
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
    if not client.is_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

    try:
        from datetime import datetime, timedelta
        now = datetime.now()

        req_data = request.json or {}
        include_classes = bool(req_data.get("include_classes", True))
        include_exams = bool(req_data.get("include_exams", True))
        include_reminders = bool(req_data.get("include_reminders", True))
        only_upcoming = bool(req_data.get("only_upcoming", True))
        selected_reminder_ids = set(req_data.get("selected_reminder_ids", []))

        yt = client.get_year_and_term()
        curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
        curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"

        all_events = []

        # 1. Fetch Schedule (if requested)
        if include_classes:
            raw_sched = client.get_schedule_semester(curr_year, curr_term)
            classes = parse_semester_schedule(raw_sched)
            cancelled_set = load_cancellations()
            classes = [c for c in classes if c.uid not in cancelled_set]
            if only_upcoming:
                classes = [c for c in classes if c.end_datetime >= now]
            all_events.extend(classes)

        # 2. Fetch Exams (if requested)
        if include_exams:
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
            seen_exam_keys = set()
            for ex in parsed_exams:
                k = (ex.title, ex.start_datetime.strftime("%Y%m%d%H%M"))
                if k not in seen_exam_keys:
                    seen_exam_keys.add(k)
                    if not only_upcoming or ex.start_datetime >= now - timedelta(hours=4):
                        all_events.append(ex)

        # 3. Add Reminders (if requested)
        if include_reminders:
            analyzer = NoticeAnalyzer(load_config())
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

            try:
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
            cal_title = "HCMUTE Thời Khóa Biểu"
        elif include_exams and not include_classes and not include_reminders:
            download_name = "hcmute_lich_thi.ics"
            cal_title = "HCMUTE Lịch Thi"
        elif include_reminders and not include_classes and not include_exams:
            download_name = "hcmute_nhac_nho.ics"
            cal_title = "HCMUTE Nhắc Nhở Hạn Chót"
        else:
            download_name = "hcmute_calendar.ics"
            cal_title = "HCMUTE Lịch Học & Thi"

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
    if not client.is_authenticated():
        return jsonify({"success": False, "message": "Chưa đăng nhập."}), 401

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
        now = datetime.now()

        req_data = request.json or {}
        include_classes = bool(req_data.get("include_classes", True))
        include_exams = bool(req_data.get("include_exams", True))
        include_reminders = bool(req_data.get("include_reminders", True))
        only_upcoming = bool(req_data.get("only_upcoming", True))
        selected_reminder_ids = set(req_data.get("selected_reminder_ids", []))

        yt = client.get_year_and_term()
        curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
        curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"

        total_created, total_updated, total_synced = 0, 0, 0
        synced_calendars = []

        # 1. Sync Classes to dedicated calendar
        if include_classes:
            raw_sched = client.get_schedule_semester(curr_year, curr_term)
            classes = parse_semester_schedule(raw_sched)
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
            seen_exam_keys = set()
            exams_to_sync = []
            for ex in parsed_exams:
                k = (ex.title, ex.start_datetime.strftime("%Y%m%d%H%M"))
                if k not in seen_exam_keys:
                    seen_exam_keys.add(k)
                    if not only_upcoming or ex.start_datetime >= now - timedelta(hours=4):
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

            try:
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


CALENDAR_FEED_CACHE_FILE = "data/calendar_feed_cache.json"


def save_feed_cache(events: list):
    """Cache parsed CalendarEvent objects to file for 24/7 background feed fetching."""
    try:
        os.makedirs("data", exist_ok=True)
        with open(CALENDAR_FEED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_feed_cache() -> list:
    """Load cached CalendarEvent objects when portal session is expired."""
    if os.path.exists(CALENDAR_FEED_CACHE_FILE):
        try:
            with open(CALENDAR_FEED_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [CalendarEvent.from_dict(d) for d in raw]
        except Exception:
            pass
    return []


@app.route("/calendar/feed.ics", methods=["GET"])
def calendar_feed():
    """
    Live webcal/iCal subscription feed.
    Automatically updates on Apple Calendar & Google Calendar in real time.
    Supports query parameters: ?classes=1&exams=1&reminders=1
    """
    from datetime import datetime, timedelta
    now = datetime.now()

    inc_classes = request.args.get("classes", "1") in ("1", "true", "True", "yes")
    inc_exams = request.args.get("exams", "1") in ("1", "true", "True", "yes")
    inc_reminders = request.args.get("reminders", "0") in ("1", "true", "True", "yes")

    client = get_client()
    fetched_fresh = False
    all_raw_events = []

    if client.is_authenticated():
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

        # Keep only upcoming events
        if is_class and ev.end_datetime < now:
            continue
        if is_exam and ev.start_datetime < now - timedelta(hours=4):
            continue
        if is_reminder and ev.start_datetime < now - timedelta(days=1):
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
        data = request.json or {}
        if "telegram" not in cfg:
            cfg["telegram"] = {}
        cfg["telegram"]["bot_token"] = data.get("bot_token", "").strip()
        cfg["telegram"]["chat_id"] = data.get("chat_id", "").strip()
        cfg["telegram"]["morning_briefing"] = bool(data.get("morning_briefing", True))
        cfg["telegram"]["morning_time"] = data.get("morning_time", "06:30").strip()
        cfg["telegram"]["diff_alert"] = bool(data.get("diff_alert", True))
        cfg["telegram"]["enabled"] = bool(data.get("enabled", True))

        save_tg_config(cfg)
        return jsonify({"success": True, "config": cfg["telegram"]})

    return jsonify({"success": True, "config": cfg.get("telegram", {})})


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
    """Mark a class session as cancelled manually."""
    data = request.json or {}
    uid = data.get("uid", "").strip()
    if not uid:
        return jsonify({"success": False, "message": "Thiếu UID buổi học."}), 400

    add_cancellation(uid)
    return jsonify({"success": True, "cancelled": list(load_cancellations())})


@app.route("/api/schedule/uncancel", methods=["POST"])
def api_schedule_uncancel():
    """Unmark a cancelled class."""
    data = request.json or {}
    uid = data.get("uid", "").strip()
    cancelled = load_cancellations()
    if uid in cancelled:
        cancelled.remove(uid)
        from src.telegram_bot import CANCELLATIONS_PATH
        with open(CANCELLATIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(list(cancelled), f, ensure_ascii=False, indent=2)
    return jsonify({"success": True, "cancelled": list(load_cancellations())})


def run_app(port: int = None, open_browser: bool = True):
    if port is None:
        port = int(os.environ.get("PORT", 5000))

    is_cloud = bool(os.environ.get("PORT"))
    host = "0.0.0.0" if is_cloud else "127.0.0.1"
    url = f"http://localhost:{port}"

    # Start Telegram background daemon
    bot_daemon = TelegramBotDaemon(get_client)
    bot_daemon.start()

    if open_browser and not is_cloud:
        Timer(1.2, lambda: webbrowser.open(url)).start()

    print(f"\n🚀 HCMUTE Calendar Sync Dashboard đang chạy tại: http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_app()
