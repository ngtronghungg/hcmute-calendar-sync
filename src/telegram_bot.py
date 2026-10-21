"""
HCMUTE Telegram Bot Service
Lightweight student assistant for Telegram notifications:
- Morning briefings (daily class & exam summary)
- Auto-alert on schedule changes (diff detection: cancellations & make-up classes)
- Interactive bot commands: /today, /tkb, /thi, /nhacnho, /nghi
Zero heavy dependencies, runs with minimal RAM (~5MB).
"""

import os
import sys
import json
import time
import threading
import requests
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

# Ensure project root in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.hcmute_api import HCMUTEClient
from src.schedule_parser import parse_semester_schedule, parse_exam_schedule, CalendarEvent
from src.notice_analyzer import NoticeAnalyzer

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.json")
SNAPSHOT_PATH = os.path.join(PROJECT_ROOT, "data", ".schedule_snapshot.json")
CANCELLATIONS_PATH = os.path.join(PROJECT_ROOT, "data", ".manual_cancellations.json")
TELEGRAM_USERS_PATH = os.path.join(PROJECT_ROOT, "data", "telegram_users.json")
CANCELLATION_VOTES_PATH = os.path.join(PROJECT_ROOT, "data", ".cancellation_votes.json")


def load_telegram_users() -> Dict[str, dict]:
    """Load all registered Telegram users: {chat_id: {mssv, name, registered_at, active...}}."""
    if os.path.exists(TELEGRAM_USERS_PATH):
        try:
            with open(TELEGRAM_USERS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_telegram_users(users: Dict[str, dict]) -> bool:
    """Save registered Telegram users to JSON."""
    try:
        os.makedirs(os.path.dirname(TELEGRAM_USERS_PATH), exist_ok=True)
        with open(TELEGRAM_USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def register_or_update_telegram_user(
    chat_id: str,
    mssv: str = "",
    name: str = "",
    username: str = "",
    morning_briefing: bool = True,
    diff_alert: bool = True,
    exam_alert: bool = True,
) -> dict:
    """Register a new student or update their settings."""
    users = load_telegram_users()
    chat_key = str(chat_id).strip()
    if not chat_key:
        return {}

    existing = users.get(chat_key, {})
    user_record = {
        "chat_id": chat_key,
        "mssv": mssv or existing.get("mssv", ""),
        "name": name or existing.get("name", "Sinh viên"),
        "username": username or existing.get("username", ""),
        "registered_at": existing.get("registered_at") or datetime.now().strftime("%d/%m/%Y %H:%M"),
        "last_active": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "active": True,
        "morning_briefing": morning_briefing,
        "diff_alert": diff_alert,
        "exam_alert": exam_alert,
    }
    users[chat_key] = user_record
    save_telegram_users(users)
    return user_record


def delete_telegram_user(chat_id: str) -> bool:
    """Remove a user from bot registration."""
    users = load_telegram_users()
    chat_key = str(chat_id).strip()
    if chat_key in users:
        del users[chat_key]
        return save_telegram_users(users)
    return False


def broadcast_telegram_message(bot_token: str, message: str) -> dict:
    """Send an announcement message to all registered active users."""
    users = load_telegram_users()
    sent_count, error_count = 0, 0
    formatted = f"📢 <b>THÔNG BÁO TỪ HỆ THỐNG HCMUTE SYNC</b>\n\n{message}"

    for chat_id, u in users.items():
        if u.get("active", True):
            success = send_telegram_message(bot_token, chat_id, formatted)
            if success:
                sent_count += 1
            else:
                error_count += 1

    return {"total": len(users), "sent": sent_count, "errors": error_count}


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_cancellations() -> set:
    if os.path.exists(CANCELLATIONS_PATH):
        try:
            with open(CANCELLATIONS_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def add_cancellation(uid_or_key: str):
    cancelled = load_cancellations()
    cancelled.add(uid_or_key)
    try:
        os.makedirs(os.path.dirname(CANCELLATIONS_PATH), exist_ok=True)
        with open(CANCELLATIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(list(cancelled), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def remove_cancellation(uid_or_key: str) -> bool:
    """Restore a previously cancelled class."""
    cancelled = load_cancellations()
    if uid_or_key in cancelled:
        cancelled.remove(uid_or_key)
        try:
            with open(CANCELLATIONS_PATH, "w", encoding="utf-8") as f:
                json.dump(list(cancelled), f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            pass
    return False


def load_cancellation_votes() -> dict:
    """Load active crowdsourced cancellation votes."""
    if os.path.exists(CANCELLATION_VOTES_PATH):
        try:
            with open(CANCELLATION_VOTES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cancellation_votes(votes: dict) -> bool:
    """Persist active crowdsourced cancellation votes."""
    try:
        os.makedirs(os.path.dirname(CANCELLATION_VOTES_PATH), exist_ok=True)
        with open(CANCELLATION_VOTES_PATH, "w", encoding="utf-8") as f:
            json.dump(votes, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def record_cancellation_vote(
    class_uid: str,
    voter_id: str,
    course_title: str = "",
    time_str: str = "",
    bot_token: Optional[str] = None,
) -> dict:
    """
    Crowdsourced Class Cancellation Voting Engine:
    Tracks student votes when a lecturer announces an unexpected cancellation on Zalo.
    When the threshold (default 80%) is reached:
    - Automatically confirms cancellation system-wide in .manual_cancellations.json
    - Automatically removes class from Google Calendar and iCal feeds for everyone
    - Broadcasts an alert to all registered students
    """
    import math
    cfg = load_config()
    crowd_cfg = cfg.get("crowdsource_cancellation", {})
    threshold_percent = crowd_cfg.get("threshold_percent", 80)
    enabled = crowd_cfg.get("enabled", True)

    users = load_telegram_users()
    active_users = [u for u in users.values() if u.get("active", True)]
    total_students = len(active_users) if active_users else 1

    needed_votes = max(1, math.ceil(total_students * (threshold_percent / 100.0)))

    if not course_title:
        try:
            from src.web_server import load_feed_cache
            for ev in load_feed_cache():
                if ev.uid == class_uid:
                    course_title = ev.title
                    time_str = f"{ev.start_datetime.strftime('%H:%M')} ngày {ev.start_datetime.strftime('%d/%m/%Y')}"
                    break
        except Exception:
            pass
    if not course_title:
        course_title = "Buổi học"

    votes_db = load_cancellation_votes()
    entry = votes_db.get(class_uid, {
        "class_uid": class_uid,
        "title": course_title,
        "time_str": time_str,
        "voters": [],
        "confirmed": False,
        "confirmed_at": "",
    })

    voter_str = str(voter_id)
    if voter_str not in entry["voters"]:
        entry["voters"].append(voter_str)

    current_votes = len(entry["voters"])
    percent = (current_votes / total_students) * 100.0

    was_confirmed = entry.get("confirmed", False)
    just_confirmed = False

    if not enabled or current_votes >= needed_votes:
        entry["confirmed"] = True
        entry["confirmed_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        add_cancellation(class_uid)
        just_confirmed = not was_confirmed

    votes_db[class_uid] = entry
    save_cancellation_votes(votes_db)

    # Broadcast to other students if threshold was just reached
    broadcast_sent = False
    if just_confirmed and bot_token and len(users) > 1:
        alert_msg = (
            f"🚨 <b>XÁC NHẬN NGHỈ HỌC CỘNG ĐỒNG (ZALO BÁO NGHỈ)</b>\n\n"
            f"📚 Môn học: <b>{course_title}</b>\n"
            f"⏰ Thời gian: <b>{time_str}</b>\n\n"
            f"⚡ <i>Đã có {current_votes}/{total_students} sinh viên ({percent:.0f}%) đồng loạt báo nghỉ học (từ Zalo lớp).</i>\n\n"
            f"✔ Hệ thống đã tự động xác nhận và <b>xóa buổi học này khỏi Google Calendar</b> của toàn bộ sinh viên!"
        )
        for chat_id, u in users.items():
            if u.get("active", True) and str(chat_id) != voter_str:
                send_telegram_message(bot_token, chat_id, alert_msg)
        broadcast_sent = True

    return {
        "status": "confirmed" if entry["confirmed"] else "pending",
        "just_confirmed": just_confirmed,
        "votes": current_votes,
        "needed": needed_votes,
        "total": total_students,
        "percent": percent,
        "threshold": threshold_percent,
        "title": course_title,
        "time_str": time_str,
        "broadcast": broadcast_sent,
    }


def remove_cancellation_vote(class_uid: str, voter_id: str, bot_token: Optional[str] = None) -> dict:
    """Remove student's vote and un-confirm if below threshold."""
    votes_db = load_cancellation_votes()
    if class_uid not in votes_db:
        remove_cancellation(class_uid)
        return {"status": "unvoted"}

    entry = votes_db[class_uid]
    voter_str = str(voter_id)
    if voter_str in entry["voters"]:
        entry["voters"].remove(voter_str)

    users = load_telegram_users()
    total_students = len(users) if users else 1
    cfg = load_config()
    threshold_percent = cfg.get("crowdsource_cancellation", {}).get("threshold_percent", 80)
    import math
    needed_votes = max(1, math.ceil(total_students * (threshold_percent / 100.0)))

    if entry.get("confirmed") and len(entry["voters"]) < needed_votes:
        entry["confirmed"] = False
        remove_cancellation(class_uid)
        if bot_token and len(users) > 1:
            restore_msg = (
                f"↩️ <b>THÔNG BÁO: KHÔI PHỤC LẠI BUỔI HỌC</b>\n\n"
                f"📚 Môn: <b>{entry.get('title', 'Buổi học')}</b>\n"
                f"⏰ Thời gian: <b>{entry.get('time_str', '')}</b>\n\n"
                f"✔ Số lượt báo nghỉ đã giảm xuống dưới {threshold_percent}%. Buổi học đã được khôi phục trở lại vào Google Calendar!"
            )
            for chat_id, u in users.items():
                if u.get("active", True):
                    send_telegram_message(bot_token, chat_id, restore_msg)

    votes_db[class_uid] = entry
    save_cancellation_votes(votes_db)
    return {"status": "unvoted", "current_votes": len(entry["voters"])}


MAKEUP_CLASSES_PATH = os.path.join(PROJECT_ROOT, "data", ".makeup_classes.json")


def load_makeup_classes() -> List[CalendarEvent]:
    """Load manually added or portal-detected make-up classes."""
    if os.path.exists(MAKEUP_CLASSES_PATH):
        try:
            with open(MAKEUP_CLASSES_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [CalendarEvent.from_dict(d) for d in raw]
        except Exception:
            pass
    return []


def save_makeup_classes(events: List[CalendarEvent]) -> bool:
    """Save make-up classes to disk."""
    try:
        os.makedirs(os.path.dirname(MAKEUP_CLASSES_PATH), exist_ok=True)
        with open(MAKEUP_CLASSES_PATH, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in events], f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add_makeup_class(title: str, start_dt: datetime, end_dt: datetime, location: str = "HCMUTE", note: str = "") -> CalendarEvent:
    """Add a new make-up class session."""
    events = load_makeup_classes()
    clean_title = title.strip()
    if not clean_title.startswith("[HỌC BÙ]"):
        clean_title = f"[HỌC BÙ] {clean_title}"
    uid = f"hcmute-makeup-{start_dt.strftime('%Y%m%d%H%M')}-{abs(hash(clean_title)) % 10000}"
    desc = (
        f"📅 LỊCH HỌC BÙ CHÍNH THỨC\n"
        f"📚 Môn: {clean_title}\n"
        f"⏰ Giờ học: {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')} (Ngày {start_dt.strftime('%d/%m/%Y')})\n"
        f"🏢 Phòng: {location}\n"
        f"📝 Ghi chú: {note or 'Lịch học bù do giảng viên hoặc cổng trường thông báo.'}\n"
        f"Hệ thống: HCMUTE Calendar Sync"
    )
    ev = CalendarEvent(
        uid=uid,
        title=clean_title,
        start_datetime=start_dt,
        end_datetime=end_dt,
        location=location,
        description=desc,
        event_type="CLASS",
        category="HỌC BÙ",
    )
    events = [e for e in events if e.uid != uid]
    events.append(ev)
    save_makeup_classes(events)
    return ev


def remove_makeup_class(uid: str) -> bool:
    """Delete a make-up class session."""
    events = load_makeup_classes()
    filtered = [e for e in events if e.uid != uid]
    if len(filtered) != len(events):
        return save_makeup_classes(filtered)
    return False


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    parse_mode: str = "HTML",
) -> bool:
    """Send a message via Telegram Bot API using pure requests."""
    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram] Send message error: {e}")
        return False


def get_cached_telegram_exams() -> list:
    """Load cached exams from disk."""
    for p in ("data/exam_cache.json", "data/calendar_feed_cache.json"):
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return [CalendarEvent.from_dict(d) for d in raw if d.get("event_type") in ("EXAM", "THI") or "THI" in d.get("title", "")]
            except Exception:
                pass
    return []


def format_today_schedule(client: HCMUTEClient) -> str:
    """Generate summary of today's classes, exams, and urgent deadlines."""
    now = datetime.now()
    today = now.date()
    today_str = today.strftime("%d/%m/%Y")

    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    day_name = weekday_names[today.weekday()]

    # 1. Classes
    today_classes = []
    if client.ensure_authenticated():
        try:
            yt = client.get_year_and_term()
            curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
            curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"

            raw_sched = client.get_schedule_semester(curr_year, curr_term)
            classes = parse_semester_schedule(raw_sched)

            cancelled_set = load_cancellations()
            today_classes = [
                c for c in classes
                if c.start_datetime.date() == today and c.uid not in cancelled_set
            ]
            today_classes.sort(key=lambda x: x.start_datetime)
        except Exception:
            pass

    # 2. Exams in next 7 days
    raw_exams = []
    if client.ensure_authenticated():
        try:
            raw_exams.extend(client.get_exams_by_time())
        except Exception:
            pass
    exams = parse_exam_schedule(raw_exams)
    if not exams:
        exams = get_cached_telegram_exams()

    upcoming_exams = [
        e for e in exams
        if 0 <= (e.start_datetime.date() - today).days <= 7
    ]
    upcoming_exams.sort(key=lambda x: x.start_datetime)

    student_name = "HCMUTE"
    if client.session_data:
        student_name = client.session_data.get("DisplayName") or client.session_data.get("FullName") or client.session_data.get("Username", "HCMUTE")

    lines = [
        f"📅 <b>LỊCH HỌC & THI HÔM NAY ({day_name}, {today_str})</b>",
        f"👤 Sinh viên: <b>{student_name}</b>\n",
    ]

    if today_classes:
        lines.append(f"📚 <b>Buổi học trên lớp ({len(today_classes)} môn):</b>")
        for c in today_classes:
            time_str = f"{c.start_datetime.strftime('%H:%M')} - {c.end_datetime.strftime('%H:%M')}"
            room = c.location.split(",")[0] if c.location else "Xem TKB"
            lines.append(f" • <b>{c.title}</b>")
            lines.append(f"   🕒 Giờ: {time_str} | 📍 {room}")
    else:
        lines.append("🏖️ <b>Hôm nay bạn không có lịch học trên lớp!</b>")

    if upcoming_exams:
        lines.append("\n🔥 <b>CẢNH BÁO LỊCH THI GẦN KỀ:</b>")
        for ex in upcoming_exams:
            diff = (ex.start_datetime.date() - today).days
            when = "HÔM NAY!" if diff == 0 else ("NGÀY MAI!" if diff == 1 else f"Còn {diff} ngày")
            lines.append(f" • <b>{ex.title}</b> ({when})")
            lines.append(f"   ⏰ {ex.start_datetime.strftime('%H:%M ngày %d/%m/%Y')} | 📍 {ex.location}")

    return "\n".join(lines)


def format_upcoming_exams(client: HCMUTEClient) -> str:
    """Generate upcoming exam schedule with disk cache fallback."""
    exams = []
    if client.ensure_authenticated():
        raw_exams = []
        try:
            raw_exams.extend(client.get_exams_by_time())
        except Exception:
            pass
        exams = parse_exam_schedule(raw_exams)

    if not exams:
        exams = get_cached_telegram_exams()

    now = datetime.now()
    # Keep exams in the last 7 days + upcoming so current exam period is clearly displayed
    upcoming = [e for e in exams if e.start_datetime >= now - timedelta(days=7)]
    upcoming.sort(key=lambda x: x.start_datetime)

    if not upcoming:
        if exams:
            exams.sort(key=lambda x: x.start_datetime, reverse=True)
            lines = [f"📝 <b>LỊCH THI GẦN NHẤT (Tổng cộng {len(exams)} môn đã có):</b>\n"]
            for ex in exams[:3]:
                lines.append(f"• <b>{ex.title}</b>")
                lines.append(f"  ⏰ {ex.start_datetime.strftime('%H:%M ngày %d/%m/%Y')} | 📍 {ex.location}")
            return "\n".join(lines)
        return "📝 <b>Hiện không có lịch thi kết thúc học phần sắp tới.</b>"

    lines = [f"📝 <b>LỊCH THI KẾT THÚC HỌC PHẦN ({len(upcoming)} môn trong đợt này)</b>\n"]
    for ex in upcoming:
        diff = (ex.start_datetime.date() - now.date()).days
        if diff < 0:
            badge = "ĐÃ THI"
        elif diff == 0:
            badge = "🔥 HÔM NAY"
        elif diff == 1:
            badge = "🔥 NGÀY MAI"
        elif diff <= 3:
            badge = f"⚡ Còn {diff} ngày"
        else:
            badge = f"📅 Còn {diff} ngày"

        lines.append(f"• <b>{ex.title}</b> [{badge}]")
        lines.append(f"  ⏰ {ex.start_datetime.strftime('%H:%M ngày %d/%m/%Y')}")
        lines.append(f"  📍 {ex.location}")
        lines.append("")

    return "\n".join(lines)


def format_reminders(client: HCMUTEClient) -> str:
    """Generate summary of active deadlines & portal notices."""
    if not client.ensure_authenticated():
        return "⚠️ Chưa đăng nhập tài khoản HCMUTE."

    analyzer = NoticeAnalyzer(load_config())
    raw_notices = client.get_personal_messages()[:15] + client.get_general_announcements()[:20]

    # Decrypt personal notice bodies
    for p in raw_notices:
        mid = p.get("MessageID") or p.get("Id")
        if mid and "MessageBody" not in p:
            try:
                detail = client.get_message_detail(mid)
                p["MessageBody"] = detail.get("MessageBody", "")
            except Exception:
                pass

    items = analyzer.analyze_all_announcements(raw_notices)
    try:
        fee = analyzer.analyze_tuition_debt(client.get_fee_debt())
        if fee:
            items.insert(0, fee)
    except Exception:
        pass

    now = datetime.now()
    future_items = [it for it in items if it.deadline and it.deadline >= now - timedelta(days=1)]
    future_items.sort(key=lambda x: x.deadline)

    if not future_items:
        return "✨ <b>Tuyệt vời! Bạn không có hạn chót gấp nào cần xử lý.</b>"

    lines = [f"⚡ <b>CÁC NHẮC NHỞ HẠN CHÓT GẦN NHẤT ({len(future_items)})</b>\n"]
    for it in future_items[:6]:
        diff = (it.deadline.date() - now.date()).days
        if diff <= 0:
            badge = "🔥 HÔM NAY HẾT HẠN"
        elif diff == 1:
            badge = "🔥 NGÀY MAI HẾT HẠN"
        else:
            badge = f"⚡ Còn {diff} ngày"

        lines.append(f"• <b>{it.title}</b>")
        lines.append(f"  ⏰ Hạn: {it.deadline.strftime('%d/%m/%Y %H:%M')} [{badge}]")
        lines.append(f"  👉 {it.action_required}")
        lines.append("")

    return "\n".join(lines)


LAST_SYNC_TIMESTAMP = 0


def auto_sync_and_detect_changes(client: HCMUTEClient, bot_token: Optional[str] = None, force: bool = False) -> dict:
    """
    Perform a complete real-time sync with HCMUTE portal:
    1. Silently re-authenticate if token expired (using saved vault).
    2. Check for removed classes (Báo nghỉ) and auto-cancel them.
    3. Check for added classes (Lịch học bù) and include them.
    4. Check for notices mentioning cancellations/make-up classes.
    5. Push instant Telegram notifications to all registered students.
    6. Update calendar cache so Google Calendar feeds reflect changes immediately.
    """
    global LAST_SYNC_TIMESTAMP
    import time
    now_ts = time.time()
    if not force and (now_ts - LAST_SYNC_TIMESTAMP < 300):
        return {"status": "skipped", "message": "Recently synced within 5 mins"}

    if not client.ensure_authenticated():
        return {"status": "unauthenticated", "message": "Chưa có thông tin phiên đăng nhập."}

    LAST_SYNC_TIMESTAMP = now_ts
    results = {"cancelled_count": 0, "makeup_count": 0, "messages_sent": 0}

    try:
        now = datetime.now()
        yt = client.get_year_and_term()
        curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
        curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"

        raw_sched = client.get_schedule_semester(curr_year, curr_term)
        current_classes = parse_semester_schedule(raw_sched)

        # Lookahead 30 days
        upcoming_curr = {
            c.uid: {
                "title": c.title,
                "start": c.start_datetime.isoformat(),
                "end": c.end_datetime.isoformat(),
                "location": c.location,
            }
            for c in current_classes
            if c.end_datetime >= now and (c.start_datetime.date() - now.date()).days <= 30
        }

        old_snapshot = {}
        if os.path.exists(SNAPSHOT_PATH):
            try:
                with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                    old_snapshot = json.load(f)
            except Exception:
                pass

        token_to_use = bot_token
        if not token_to_use:
            cfg = load_config()
            token_to_use = cfg.get("telegram", {}).get("bot_token", "")

        users = load_telegram_users()

        if old_snapshot:
            # 1. Classes that disappeared from the portal (BÁO NGHỈ)
            removed_uids = [uid for uid in old_snapshot if uid not in upcoming_curr]
            for uid in removed_uids:
                info = old_snapshot[uid]
                add_cancellation(uid)
                results["cancelled_count"] += 1
                dt_str = info.get("start", "")
                try:
                    dt = datetime.fromisoformat(dt_str)
                    date_display = dt.strftime("%d/%m/%Y lúc %H:%M")
                except Exception:
                    date_display = dt_str

                alert_text = (
                    f"⚠️ <b>CẢNH BÁO BÁO NGHỈ TỪ CỔNG TRƯỜNG:</b>\n\n"
                    f"📚 Môn: <b>{info.get('title')}</b>\n"
                    f"⏰ Buổi học ngày <b>{date_display}</b> đã được cập nhật NGHỈ trên hệ thống cổng trường!\n"
                    f"✔ Google Calendar & Apple Calendar của bạn đã tự động xóa buổi học này."
                )
                if token_to_use:
                    for uid_chat, u in users.items():
                        if u.get("diff_alert", True):
                            send_telegram_message(token_to_use, uid_chat, alert_text)
                            results["messages_sent"] += 1

            # 2. Classes that were newly added on the portal (LỊCH HỌC BÙ)
            added_uids = [uid for uid in upcoming_curr if uid not in old_snapshot]
            for uid in added_uids:
                info = upcoming_curr[uid]
                results["makeup_count"] += 1
                dt_str = info.get("start", "")
                try:
                    dt = datetime.fromisoformat(dt_str)
                    date_display = dt.strftime("%d/%m/%Y lúc %H:%M")
                except Exception:
                    date_display = dt_str

                alert_text = (
                    f"📅 <b>LỊCH HỌC BÙ MỚI TỪ CỔNG TRƯỜNG:</b>\n\n"
                    f"📚 Môn: <b>{info.get('title')}</b>\n"
                    f"⏰ Thời gian: <b>{date_display}</b>\n"
                    f"📍 Địa điểm: <b>{info.get('location')}</b>\n"
                    f"✔ Lịch học bù đã được tự động thêm vào Google Calendar của bạn!"
                )
                if token_to_use:
                    for uid_chat, u in users.items():
                        if u.get("diff_alert", True):
                            send_telegram_message(token_to_use, uid_chat, alert_text)
                            results["messages_sent"] += 1

        # Save new snapshot
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(upcoming_curr, f, ensure_ascii=False, indent=2)

        # Update calendar feed cache (merging makeup classes)
        from src.web_server import save_feed_cache, load_cached_exams
        all_events_to_cache = list(current_classes)
        all_events_to_cache.extend(load_cached_exams())
        all_events_to_cache.extend(load_makeup_classes())
        save_feed_cache(all_events_to_cache)

        results["status"] = "success"
    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)

    return results


def check_schedule_changes(client: HCMUTEClient, bot_token: str, chat_id: str) -> None:
    """Backwards compatibility alias."""
    auto_sync_and_detect_changes(client, bot_token=bot_token, force=True)


class TelegramBotDaemon:
    """Lightweight background thread handling Telegram commands & periodic checks."""

    def __init__(self, get_client_func):
        self.get_client = get_client_func
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_update_id = 0
        self.last_morning_date: Optional[date] = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        print("🤖 Telegram Bot Background Service đã khởi động.")

    def stop(self):
        self.running = False

    def _run_loop(self):
        while self.running:
            try:
                cfg = load_config()
                tg_cfg = cfg.get("telegram", {})
                enabled = tg_cfg.get("enabled", False)
                bot_token = tg_cfg.get("bot_token", "").strip()
                chat_id = tg_cfg.get("chat_id", "").strip()

                if not enabled or not bot_token:
                    time.sleep(10)
                    continue

                client = self.get_client()

                # 1. Process Telegram Updates (Long Polling)
                self._poll_updates(bot_token, client)

                # 2. Check Morning Briefing for all registered students
                now = datetime.now()
                morning_target = tg_cfg.get("morning_time", "06:30")
                try:
                    target_h, target_m = map(int, morning_target.split(":"))
                except Exception:
                    target_h, target_m = 6, 30

                if now.date() != self.last_morning_date:
                    if (now.hour > target_h) or (now.hour == target_h and now.minute >= target_m):
                        trigger_morning_briefing(client, bot_token=bot_token)
                        self.last_morning_date = now.date()

                        # Run diff check once a day after morning briefing
                        if tg_cfg.get("diff_alert", True):
                            auto_sync_and_detect_changes(client, bot_token=bot_token)

            except Exception as e:
                time.sleep(5)

            time.sleep(2)

    def _poll_updates(self, bot_token: str, client: HCMUTEClient):
        """Fetch and handle commands like /start, /today, /tkb, /thi, /nhacnho, /nghi."""
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 2}

        try:
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return

            data = resp.json()
            if not data.get("ok"):
                return

            for upd in data.get("result", []):
                self.last_update_id = upd.get("update_id", self.last_update_id)
                process_telegram_update(upd, bot_token, client)
        except Exception:
            pass


MAIN_INLINE_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "📅 Lịch hôm nay", "callback_data": "action_today"},
            {"text": "📚 5 buổi học tới", "callback_data": "action_tkb"},
        ],
        [
            {"text": "🎯 Lịch thi", "callback_data": "action_thi"},
            {"text": "❌ Báo nghỉ môn", "callback_data": "action_nghi"},
        ],
        [
            {"text": "🔄 Đồng bộ realtime", "callback_data": "action_sync"},
        ],
    ]
}


def trigger_morning_briefing(client: HCMUTEClient, bot_token: Optional[str] = None) -> dict:
    """Send morning briefing to all registered active users."""
    if not bot_token:
        cfg = load_config().get("telegram", {})
        bot_token = cfg.get("bot_token", "").strip()
    if not bot_token:
        return {"total": 0, "sent": 0, "errors": 0, "message": "Bot token not configured"}

    users = load_telegram_users()
    briefing_text = format_today_schedule(client)
    formatted = f"🌅 <b>BẢN TIN BUỔI SÁNG HCMUTE</b>\n\n{briefing_text}"
    sent, errors = 0, 0
    for chat_id, u in users.items():
        if u.get("active", True) and u.get("morning_briefing", True):
            if send_telegram_message(bot_token, chat_id, formatted, reply_markup=MAIN_INLINE_KEYBOARD):
                sent += 1
            else:
                errors += 1
    return {"total": len(users), "sent": sent, "errors": errors}


def format_upcoming_classes(client: HCMUTEClient, limit: int = 5) -> str:
    """Format the next upcoming classes for students."""
    now = datetime.now()
    classes = []
    if client.ensure_authenticated():
        try:
            yt = client.get_year_and_term()
            curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
            curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"
            raw_sched = client.get_schedule_semester(curr_year, curr_term)
            classes = parse_semester_schedule(raw_sched)
        except Exception:
            pass
    if not classes:
        try:
            from src.web_server import load_feed_cache
            classes = [c for c in load_feed_cache() if (c.event_type or "").upper() in ("CLASS", "TKB")]
        except Exception:
            classes = []

    cancelled_set = load_cancellations()
    valid_classes = [c for c in classes if c.uid not in cancelled_set]

    upcoming = [c for c in valid_classes if c.end_datetime >= now]
    upcoming.sort(key=lambda x: x.start_datetime)

    if not upcoming:
        return "🏖️ Bạn không còn buổi học nào trong kỳ này."

    lines = [f"📚 <b>{min(limit, len(upcoming))} BUỔI HỌC SẮP TỚI:</b>\n"]
    for c in upcoming[:limit]:
        day_str = c.start_datetime.strftime("%d/%m/%Y")
        time_str = f"{c.start_datetime.strftime('%H:%M')} - {c.end_datetime.strftime('%H:%M')}"
        room = c.location.split(",")[0]
        diff = (c.start_datetime.date() - now.date()).days
        countdown = "Hôm nay" if diff == 0 else (f"Còn {diff} ngày" if diff > 0 else "")
        lines.append(f"• <b>{c.title}</b> ({countdown})")
        lines.append(f"  📅 {day_str} | ⏰ {time_str} | 📍 {room}\n")
    return "\n".join(lines)


def _send_cancellation_picker(bot_token: str, chat_id: str, client: HCMUTEClient):
    """Present 1-tap interactive inline buttons for classes in the next 7 days."""
    now = datetime.now()
    today = now.date()
    client.ensure_authenticated()
    from src.web_server import load_feed_cache
    all_events = load_feed_cache()
    classes = [e for e in all_events if (e.event_type or "").upper() in ("CLASS", "TKB")]
    cancelled_set = load_cancellations()
    upcoming_7d = [c for c in classes if 0 <= (c.start_datetime.date() - today).days <= 7]
    upcoming_7d.sort(key=lambda x: x.start_datetime)

    if not upcoming_7d:
        send_telegram_message(bot_token, chat_id, "🏖️ Bạn không có buổi học nào trong 7 ngày tới.")
    else:
        votes_db = load_cancellation_votes()
        users_cnt = len(load_telegram_users()) or 1
        buttons = []
        for c in upcoming_7d[:8]:
            diff = (c.start_datetime.date() - today).days
            when = "Hôm nay" if diff == 0 else f"{c.start_datetime.strftime('%d/%m')}"
            title_short = c.title.replace("[HỌC BÙ] ", "")[:20]
            v_entry = votes_db.get(c.uid, {})
            v_count = len(v_entry.get("voters", []))
            if c.uid in cancelled_set:
                buttons.append([{"text": f"✅ {title_short} ({when}) - ĐÃ NGHỈ", "callback_data": f"restore_class:{c.uid}"}])
            else:
                vote_badge = f" [🗳️ {v_count}/{users_cnt}]" if (v_count > 0 and users_cnt > 1) else ""
                buttons.append([{"text": f"❌ Báo nghỉ: {title_short} ({when}){vote_badge}", "callback_data": f"cancel_class:{c.uid}"}])

        reply_markup = {"inline_keyboard": buttons}
        send_telegram_message(
            bot_token,
            chat_id,
            "👇 <b>Chọn buổi học giảng viên báo nghỉ để tự động xóa khỏi Google Calendar:</b>",
            reply_markup=reply_markup
        )


def _send_restore_picker(bot_token: str, chat_id: str):
    """Present 1-tap buttons to restore previously cancelled classes."""
    cancelled_set = load_cancellations()
    if not cancelled_set:
        send_telegram_message(bot_token, chat_id, "✔ Hiện không có môn nào đang ở trạng thái báo nghỉ.")
    else:
        from src.web_server import load_feed_cache
        all_events = load_feed_cache()
        ev_map = {e.uid: e for e in all_events}
        buttons = []
        for uid in list(cancelled_set)[:8]:
            ev = ev_map.get(uid)
            title_txt = ev.title[:20] if ev else "Buổi học"
            date_txt = ev.start_datetime.strftime("%d/%m") if ev else ""
            buttons.append([{"text": f"↩️ Khôi phục: {title_txt} ({date_txt})", "callback_data": f"restore_class:{uid}"}])
        send_telegram_message(
            bot_token,
            chat_id,
            "👇 Chọn buổi học bạn muốn khôi phục lại lịch:",
            reply_markup={"inline_keyboard": buttons}
        )


def _handle_makeup_class_input(bot_token: str, chat_id: str, payload: str):
    """Parse and record make-up class from student input."""
    import re
    try:
        parts = [p.strip() for p in payload.split(",") if p.strip()]
        sub_name = parts[0]
        raw_d = parts[1] if len(parts) > 1 else ""
        raw_t = parts[2] if len(parts) > 2 else "07:30"
        room = parts[3] if len(parts) > 3 else "HCMUTE"

        m_d = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?", raw_d)
        if not m_d:
            send_telegram_message(bot_token, chat_id, "⚠️ Không nhận diện được ngày học. Vui lòng nhập ngày dạng dd/mm (ví dụ: 25/10).")
            return

        d_val, m_val = int(m_d.group(1)), int(m_d.group(2))
        y_val = int(m_d.group(3)) if m_d.group(3) else datetime.now().year

        m_t = re.search(r"(\d{1,2})(?:[:gh](\d{2}))?", raw_t)
        hh = int(m_t.group(1)) if m_t else 7
        mm = int(m_t.group(2)) if m_t and m_t.group(2) else 30

        st = datetime(y_val, m_val, d_val, hh, mm)
        en = st + timedelta(hours=3, minutes=45)
        ev = add_makeup_class(sub_name, st, en, location=room)

        confirm = (
            f"✅ <b>ĐÃ THÊM LỊCH HỌC BÙ THÀNH CÔNG!</b>\n\n"
            f"📚 Môn: <b>{ev.title}</b>\n"
            f"⏰ Thời gian: <b>{st.strftime('%H:%M')} - {en.strftime('%H:%M')} ngày {st.strftime('%d/%m/%Y')}</b>\n"
            f"📍 Phòng: <b>{room}</b>\n\n"
            f"✔ Buổi học bù này đã được đồng bộ trực tiếp vào Google Calendar và Apple Calendar của bạn!"
        )
        send_telegram_message(bot_token, chat_id, confirm, reply_markup=MAIN_INLINE_KEYBOARD)
    except Exception as err:
        send_telegram_message(bot_token, chat_id, f"⚠️ Lỗi cú pháp: {err}. Hãy thử: <code>/bu Tên môn, 25/10, 07:30, C501</code>")


def _handle_login_command(bot_token: str, chat_id: str, text: str, client: HCMUTEClient, full_name: str, tg_username: str):
    """Handle /login credentials."""
    parts = text.split()
    if len(parts) < 3:
        send_telegram_message(bot_token, chat_id, "ℹ️ Cú pháp đăng nhập trực tiếp:\n<code>/login &lt;MSSV&gt; &lt;Mật khẩu&gt;</code>\n\nSau khi đăng nhập 1 lần, tool sẽ tự động duy trì đồng bộ suốt kỳ.")
    else:
        u_in, p_in = parts[1].strip(), parts[2].strip()
        try:
            p_res = client.login(u_in, p_in)
            register_or_update_telegram_user(
                chat_id=chat_id,
                mssv=u_in,
                name=p_res.get("FullName", u_in),
                username=tg_username,
            )
            send_telegram_message(
                bot_token,
                chat_id,
                f"✅ <b>ĐĂNG NHẬP THÀNH CÔNG!</b>\n\n"
                f"👤 Sinh viên: <b>{p_res.get('FullName', u_in)}</b>\n"
                f"🎓 MSSV: <b>{u_in}</b>\n\n"
                f"✔ Phiên đăng nhập đã được lưu bền vững. Bạn không cần phải mở web nữa, bot sẽ tự động thông báo lịch học, lịch thi và báo nghỉ/bù suốt cả kỳ!",
                reply_markup=MAIN_INLINE_KEYBOARD
            )
        except Exception as err:
            send_telegram_message(bot_token, chat_id, f"❌ Đăng nhập thất bại: {err}")


def process_telegram_update(upd: dict, bot_token: str, client: HCMUTEClient) -> None:
    """
    Seamless, unified processor for incoming Telegram updates.
    Handles callback queries, deep-links, 8-digit MSSV, phone numbers,
    natural keywords, and slash commands with zero cognitive friction.
    """
    # 1. Handle Inline Button Clicks (Callback Queries)
    if "callback_query" in upd:
        cb = upd["callback_query"]
        cb_id = cb.get("id")
        cb_data = cb.get("data", "")
        from_user = cb.get("from", {})
        user_chat_id = str(from_user.get("id"))

        if cb_data.startswith("cancel_class:"):
            class_uid = cb_data.replace("cancel_class:", "")
            res = record_cancellation_vote(class_uid, user_chat_id, bot_token=bot_token)
            if res.get("status") == "confirmed":
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": "✔ Đã xác nhận nghỉ học thành công!"}
                )
                msg_txt = (
                    f"✅ <b>ĐÃ XÁC NHẬN NGHỈ HỌC THÀNH CÔNG!</b>\n\n"
                    f"📚 Môn: <b>{res.get('title')}</b>\n"
                    f"⏰ Thời gian: <b>{res.get('time_str')}</b>\n"
                    f"📊 Tỉ lệ đồng thuận: <b>{res.get('votes')}/{res.get('total')} sinh viên ({res.get('percent'):.0f}%)</b>\n\n"
                    f"✔ Buổi học này đã được hệ thống <b>tự động xóa khỏi Google Calendar & Apple Calendar</b> của toàn bộ sinh viên!"
                )
                send_telegram_message(bot_token, user_chat_id, msg_txt, reply_markup=MAIN_INLINE_KEYBOARD)
            else:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": f"🗳️ Đã ghi nhận biểu quyết ({res.get('votes')}/{res.get('needed')} bạn)!"}
                )
                msg_txt = (
                    f"🗳️ <b>ĐÃ GHI NHẬN Ý KIẾN BÁO NGHỈ CỦA BẠN!</b>\n\n"
                    f"📚 Môn: <b>{res.get('title')}</b>\n"
                    f"⏰ Thời gian: <b>{res.get('time_str')}</b>\n"
                    f"📊 Tiến độ xác nhận: <b>{res.get('votes')}/{res.get('needed')} bạn ({res.get('percent'):.0f}%)</b>\n\n"
                    f"<i>Khi đạt tỉ lệ {res.get('threshold')}% sinh viên cùng lớp báo nghỉ (thầy nhắn qua Zalo), bot sẽ tự động thông báo đến tất cả mọi người và xóa buổi học khỏi Google Calendar!</i>"
                )
                send_telegram_message(bot_token, user_chat_id, msg_txt, reply_markup=MAIN_INLINE_KEYBOARD)

        elif cb_data.startswith("restore_class:"):
            class_uid = cb_data.replace("restore_class:", "")
            remove_cancellation_vote(class_uid, user_chat_id, bot_token=bot_token)
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                json={"callback_query_id": cb_id, "text": "✔ Đã hủy biểu quyết báo nghỉ!"}
            )
            send_telegram_message(
                bot_token,
                user_chat_id,
                "✅ <b>Đã hủy biểu quyết báo nghỉ!</b>\nBuổi học đã được khôi phục trở lại vào lịch của bạn.",
                reply_markup=MAIN_INLINE_KEYBOARD
            )
        elif cb_data == "action_today":
            requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery", json={"callback_query_id": cb_id})
            send_telegram_message(bot_token, user_chat_id, format_today_schedule(client), reply_markup=MAIN_INLINE_KEYBOARD)
        elif cb_data == "action_tkb":
            requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery", json={"callback_query_id": cb_id})
            send_telegram_message(bot_token, user_chat_id, format_upcoming_classes(client), reply_markup=MAIN_INLINE_KEYBOARD)
        elif cb_data == "action_thi":
            requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery", json={"callback_query_id": cb_id})
            send_telegram_message(bot_token, user_chat_id, format_upcoming_exams(client), reply_markup=MAIN_INLINE_KEYBOARD)
        elif cb_data == "action_nghi":
            requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery", json={"callback_query_id": cb_id})
            _send_cancellation_picker(bot_token, user_chat_id, client)
        elif cb_data == "action_sync":
            requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery", json={"callback_query_id": cb_id})
            send_telegram_message(bot_token, user_chat_id, "⏳ Đang kết nối Cổng trường HCMUTE để quét cập nhật...")
            res = auto_sync_and_detect_changes(client, bot_token=bot_token, force=True)
            msg_sync = (
                f"🔄 <b>KẾT QUẢ ĐỒNG BỘ REALTIME:</b>\n\n"
                f"• Trạng thái: <b>{res.get('status', 'OK').upper()}</b>\n"
                f"• Môn học phát hiện báo nghỉ: <b>{res.get('cancelled_count', 0)}</b>\n"
                f"• Môn học bù mới phát hiện: <b>{res.get('makeup_count', 0)}</b>\n\n"
                f"✔ Toàn bộ lịch Google Calendar và Apple Calendar đã được cập nhật bản mới nhất!"
            )
            send_telegram_message(bot_token, user_chat_id, msg_sync, reply_markup=MAIN_INLINE_KEYBOARD)
        return

    # 2. Handle Text Messages
    msg = upd.get("message", {})
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    if not text or not chat_id:
        return

    from_user = msg.get("from", {})
    first_name = from_user.get("first_name", "")
    last_name = from_user.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip() or "Sinh viên"
    tg_username = from_user.get("username", "")

    # Seamless Feature: Auto-detect MSSV or Phone Number (7 to 11 digits)
    import re
    digit_match = re.match(r"^(\d{7,11})$", text)
    if digit_match:
        student_id_or_phone = digit_match.group(1)
        register_or_update_telegram_user(
            chat_id=chat_id,
            mssv=student_id_or_phone,
            name=full_name,
            username=tg_username,
        )
        welcome_lines = [
            f"🎉 <b>KÍCH HOẠT THÔNG BÁO TỰ ĐỘNG THÀNH CÔNG!</b>",
            f"👤 Sinh viên: <b>{full_name}</b>",
            f"🎓 MSSV/SĐT: <code>{student_id_or_phone}</code>\n",
            "🌟 <b>Trợ lý tự động hoàn toàn (Set & Forget):</b>",
            "• ☀️ <b>06:30 sáng:</b> Tự gửi lịch học & phòng học hôm nay",
            "• 🔥 <b>Cận kề kỳ thi:</b> Tự động nhắc phòng thi & giờ thi",
            "• ⚠️ <b>Báo nghỉ/bù:</b> Cảnh báo ngay lập tức nếu giảng viên đổi lịch\n",
            "<i>Bạn không cần mở web hay nhớ lệnh nào nữa!</i>",
        ]
        send_telegram_message(bot_token, chat_id, "\n".join(welcome_lines), reply_markup=MAIN_INLINE_KEYBOARD)
        today_text = format_today_schedule(client)
        send_telegram_message(bot_token, chat_id, today_text)
        return

    cmd = text.split()[0].lower()
    text_lower = text.lower()

    if cmd == "/start":
        parts = text.split()
        mssv_link = parts[1].strip() if len(parts) > 1 else ""
        register_or_update_telegram_user(
            chat_id=chat_id,
            mssv=mssv_link,
            name=full_name,
            username=tg_username,
        )
        welcome_lines = [
            f"👋 <b>Xin chào {full_name}!</b>",
            "Trợ lý <b>HCMUTE Calendar & Realtime Sync</b> đã kết nối thành công!\n",
            "🌟 <b>Hệ thống hoạt động hoàn toàn tự động:</b>",
            "• ☀️ <b>06:30 sáng:</b> Tự gửi lịch học & phòng học hôm nay",
            "• 🔥 <b>Cận kề kỳ thi:</b> Tự động nhắc phòng thi & giờ thi",
            "• ⚠️ <b>Báo nghỉ/bù:</b> Báo ngay khi giảng viên đổi phòng/lịch\n",
            "<i>Bạn chỉ cần bấm nút bên dưới để xem thông tin bất kỳ lúc nào:</i>"
        ]
        send_telegram_message(bot_token, chat_id, "\n".join(welcome_lines), reply_markup=MAIN_INLINE_KEYBOARD)
        today_text = format_today_schedule(client)
        send_telegram_message(bot_token, chat_id, today_text)
        return

    elif cmd in ("/today", "/homnay") or any(w in text_lower for w in ["hôm nay", "hom nay", "nay học gì", "lịch nay"]):
        send_telegram_message(bot_token, chat_id, format_today_schedule(client), reply_markup=MAIN_INLINE_KEYBOARD)

    elif cmd in ("/thi", "/lichthi", "/exam") or any(w in text_lower for w in ["lịch thi", "lich thi", "khi nào thi", "thi cử"]):
        send_telegram_message(bot_token, chat_id, format_upcoming_exams(client), reply_markup=MAIN_INLINE_KEYBOARD)

    elif cmd in ("/tkb", "/schedule") or any(w in text_lower for w in ["thời khóa biểu", "tkb", "lịch học", "lich hoc", "môn học"]):
        send_telegram_message(bot_token, chat_id, format_upcoming_classes(client), reply_markup=MAIN_INLINE_KEYBOARD)

    elif cmd in ("/nhacnho", "/deadline", "/thongbao") or any(w in text_lower for w in ["nhắc nhở", "deadline", "học phí", "shcd"]):
        send_telegram_message(bot_token, chat_id, format_reminders(client), reply_markup=MAIN_INLINE_KEYBOARD)

    elif cmd in ("/nghi", "/baonghi") or any(w in text_lower for w in ["báo nghỉ", "bao nghi", "nghỉ học", "nghi hoc"]):
        _send_cancellation_picker(bot_token, chat_id, client)

    elif cmd in ("/bu", "/hocbu") or any(w in text_lower for w in ["học bù", "hoc bu"]):
        payload = re.sub(r"^/(?:bu|hocbu)\s*", "", text, flags=re.IGNORECASE).strip()
        if not payload or payload.lower() in ["học bù", "hoc bu"]:
            guide = (
                "📅 <b>THÊM LỊCH HỌC BÙ VÀO GOOGLE CALENDAR:</b>\n\n"
                "Nhắn cú pháp cực ngắn (ngăn cách bằng dấu phẩy):\n"
                "<code>/bu &lt;Tên môn&gt;, &lt;Ngày dd/mm&gt;, &lt;Giờ hh:mm&gt;, &lt;Phòng&gt;</code>\n\n"
                "<i>Ví dụ:</i>\n"
                "<code>/bu Công nghệ CTM, 25/10, 07:30, C501</code>\n\n"
                "✔ Buổi bù sẽ xuất hiện ngay lập tức trên Google Calendar & Apple Calendar!"
            )
            send_telegram_message(bot_token, chat_id, guide)
        else:
            _handle_makeup_class_input(bot_token, chat_id, payload)

    elif cmd in ("/khoiphuc", "/uncancel"):
        _send_restore_picker(bot_token, chat_id)

    elif cmd in ("/sync", "/dongbo"):
        send_telegram_message(bot_token, chat_id, "⏳ Đang kết nối Cổng trường HCMUTE để quét cập nhật TKB & Lịch thi...")
        res = auto_sync_and_detect_changes(client, bot_token=bot_token, force=True)
        msg_sync = (
            f"🔄 <b>KẾT QUẢ ĐỒNG BỘ REALTIME:</b>\n\n"
            f"• Trạng thái: <b>{res.get('status', 'OK').upper()}</b>\n"
            f"• Buổi học phát hiện báo nghỉ: <b>{res.get('cancelled_count', 0)}</b>\n"
            f"• Buổi học bù mới phát hiện: <b>{res.get('makeup_count', 0)}</b>\n\n"
            f"✔ Toàn bộ lịch Google Calendar và Apple Calendar đã được cập nhật phiên bản mới nhất!"
        )
        send_telegram_message(bot_token, chat_id, msg_sync, reply_markup=MAIN_INLINE_KEYBOARD)

    elif cmd == "/login":
        _handle_login_command(bot_token, chat_id, text, client, full_name, tg_username)

    else:
        reply = (
            f"👋 <b>Xin chào {full_name}!</b>\n\n"
            "Trợ lý HCMUTE luôn sẵn sàng hỗ trợ bạn. Bạn có thể chọn nhanh tác vụ bên dưới:"
        )
        send_telegram_message(bot_token, chat_id, reply, reply_markup=MAIN_INLINE_KEYBOARD)
