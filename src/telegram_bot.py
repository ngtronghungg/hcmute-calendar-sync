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


def format_today_schedule(client: HCMUTEClient) -> str:
    """Generate summary of today's classes, exams, and urgent deadlines."""
    if not client.is_authenticated():
        return "⚠️ Chưa đăng nhập tài khoản HCMUTE. Vui lòng mở tool để lưu phiên."

    now = datetime.now()
    today = now.date()
    today_str = today.strftime("%d/%m/%Y")

    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    day_name = weekday_names[today.weekday()]

    # 1. Classes
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

    # 2. Exams in next 3 days
    raw_exams = []
    try:
        raw_exams.extend(client.get_exams_by_time())
    except Exception:
        pass
    exams = parse_exam_schedule(raw_exams)

    upcoming_exams = [
        e for e in exams
        if 0 <= (e.start_datetime.date() - today).days <= 3
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
    """Generate upcoming exam schedule."""
    if not client.is_authenticated():
        return "⚠️ Chưa đăng nhập tài khoản HCMUTE."

    raw_exams = []
    try:
        raw_exams.extend(client.get_exams_by_time())
    except Exception:
        pass
    exams = parse_exam_schedule(raw_exams)

    now = datetime.now()
    upcoming = [e for e in exams if e.start_datetime >= now - timedelta(hours=4)]
    upcoming.sort(key=lambda x: x.start_datetime)

    if not upcoming:
        return "📝 <b>Hiện không có lịch thi kết thúc học phần sắp tới.</b>"

    lines = [f"📝 <b>LỊCH THI KẾT THÚC HỌC PHẦN ({len(upcoming)} môn)</b>\n"]
    for ex in upcoming:
        diff = (ex.start_datetime.date() - now.date()).days
        if diff == 0:
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
    if not client.is_authenticated():
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


def check_schedule_changes(client: HCMUTEClient, bot_token: str, chat_id: str) -> None:
    """
    Diff detection: compares current portal schedule with cached snapshot.
    Alerts user on Telegram if classes are moved, cancelled, or make-up added.
    """
    if not bot_token or not chat_id or not client.is_authenticated():
        return

    try:
        yt = client.get_year_and_term()
        curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
        curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"

        raw_sched = client.get_schedule_semester(curr_year, curr_term)
        current_classes = parse_semester_schedule(raw_sched)

        now = datetime.now()
        # Look ahead 14 days
        upcoming_curr = {
            c.uid: {
                "title": c.title,
                "start": c.start_datetime.isoformat(),
                "location": c.location,
            }
            for c in current_classes
            if c.end_datetime >= now and (c.start_datetime.date() - now.date()).days <= 14
        }

        old_snapshot = {}
        if os.path.exists(SNAPSHOT_PATH):
            try:
                with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                    old_snapshot = json.load(f)
            except Exception:
                pass

        if old_snapshot:
            # Check removed classes (Cancellations)
            removed = [uid for uid in old_snapshot if uid not in upcoming_curr]
            for uid in removed:
                info = old_snapshot[uid]
                dt_str = info.get("start", "")
                try:
                    dt = datetime.fromisoformat(dt_str)
                    date_display = dt.strftime("%d/%m/%Y (%H:%M)")
                except Exception:
                    date_display = dt_str

                msg = (
                    f"⚠️ <b>CẢNH BÁO BÁO NGHỈ / DỜI LỊCH:</b>\n"
                    f"Môn: <b>{info.get('title')}</b>\n"
                    f"Buổi học ngày <b>{date_display}</b> đã được cập nhật NGHỈ trên hệ thống cổng trường HCMUTE!"
                )
                send_telegram_message(bot_token, chat_id, msg)

            # Check new classes (Make-up classes)
            added = [uid for uid in upcoming_curr if uid not in old_snapshot]
            for uid in added:
                info = upcoming_curr[uid]
                dt_str = info.get("start", "")
                try:
                    dt = datetime.fromisoformat(dt_str)
                    date_display = dt.strftime("%d/%m/%Y lúc %H:%M")
                except Exception:
                    date_display = dt_str

                msg = (
                    f"📅 <b>LỊCH HỌC BÙ MỚI ĐƯỢC XẾP:</b>\n"
                    f"Môn: <b>{info.get('title')}</b>\n"
                    f"Thời gian: <b>{date_display}</b>\n"
                    f"Địa điểm: <b>{info.get('location')}</b>"
                )
                send_telegram_message(bot_token, chat_id, msg)

        # Save new snapshot
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(upcoming_curr, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"[Telegram Diff] Error: {e}")


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

                # 2. Check Morning Briefing
                now = datetime.now()
                if tg_cfg.get("morning_briefing", True) and chat_id:
                    # Target 06:30
                    morning_target = tg_cfg.get("morning_time", "06:30")
                    target_h, target_m = map(int, morning_target.split(":"))

                    if now.date() != self.last_morning_date:
                        if (now.hour > target_h) or (now.hour == target_h and now.minute >= target_m):
                            briefing_text = format_today_schedule(client)
                            send_telegram_message(bot_token, chat_id, f"🌅 <b>BẢN TIN BUỔI SÁNG HCMUTE</b>\n\n{briefing_text}")
                            self.last_morning_date = now.date()

                            # Run diff check once a day after morning briefing
                            if tg_cfg.get("diff_alert", True):
                                check_schedule_changes(client, bot_token, chat_id)

            except Exception as e:
                # Sleep and continue, never crash the app
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

                # Handle callback queries (Inline buttons)
                if "callback_query" in upd:
                    cb = upd["callback_query"]
                    cb_id = cb.get("id")
                    cb_data = cb.get("data", "")
                    from_user = cb.get("from", {})
                    user_chat_id = str(from_user.get("id"))

                    if cb_data.startswith("cancel_class:"):
                        class_uid = cb_data.replace("cancel_class:", "")
                        add_cancellation(class_uid)
                        # Answer callback
                        requests.post(
                            f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                            json={"callback_query_id": cb_id, "text": "✔ Đã ghi nhận báo nghỉ môn học!"}
                        )
                        send_telegram_message(
                            bot_token,
                            user_chat_id,
                            f"✅ <b>Đã đánh dấu báo nghỉ thành công!</b>\nBuổi học này đã được xóa khỏi lịch nhắc nhở hôm nay."
                        )
                    continue

                msg = upd.get("message", {})
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat", {})
                chat_id = str(chat.get("id", ""))

                if not text or not chat_id:
                    continue

                # Auto-save Chat ID if not set
                cfg = load_config()
                if "telegram" not in cfg:
                    cfg["telegram"] = {}
                if not cfg["telegram"].get("chat_id"):
                    cfg["telegram"]["chat_id"] = chat_id
                    save_config(cfg)

                cmd = text.split()[0].lower()

                if cmd == "/start":
                    welcome = (
                        f"👋 <b>Xin chào {msg.get('from', {}).get('first_name', '')}!</b>\n"
                        f"Trợ lý <b>HCMUTE Calendar & Reminder</b> đã kết nối thành công với tài khoản Telegram của bạn.\n\n"
                        f"📱 <b>Các lệnh nhanh:</b>\n"
                        f"• /today - Lịch học & thi hôm nay\n"
                        f"• /tkb - Các buổi học sắp tới\n"
                        f"• /thi - Lịch thi kết thúc học phần\n"
                        f"• /nhacnho - Hạn nộp học phí & thông báo khẩn\n"
                        f"• /nghi - Báo nghỉ môn học hôm nay\n\n"
                        f"🔔 <i>Bot sẽ tự động gửi Bản tin 06:30 sáng và báo động đỏ trước giờ thi!</i>"
                    )
                    send_telegram_message(bot_token, chat_id, welcome)

                elif cmd in ("/today", "/homnay"):
                    res = format_today_schedule(client)
                    send_telegram_message(bot_token, chat_id, res)

                elif cmd in ("/thi", "/lichthi", "/exam"):
                    res = format_upcoming_exams(client)
                    send_telegram_message(bot_token, chat_id, res)

                elif cmd in ("/nhacnho", "/deadline", "/thongbao"):
                    res = format_reminders(client)
                    send_telegram_message(bot_token, chat_id, res)

                elif cmd in ("/tkb", "/schedule"):
                    now = datetime.now()
                    yt = client.get_year_and_term()
                    curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
                    curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"
                    raw_sched = client.get_schedule_semester(curr_year, curr_term)
                    classes = parse_semester_schedule(raw_sched)

                    upcoming = [c for c in classes if c.end_datetime >= now]
                    upcoming.sort(key=lambda x: x.start_datetime)

                    if not upcoming:
                        send_telegram_message(bot_token, chat_id, "🏖️ Bạn không còn buổi học nào trong kỳ này.")
                    else:
                        lines = [f"📚 <b>5 BUỔI HỌC SẮP TỚI GẦN NHẤT:</b>\n"]
                        for c in upcoming[:5]:
                            day_str = c.start_datetime.strftime("%d/%m/%Y")
                            time_str = f"{c.start_datetime.strftime('%H:%M')} - {c.end_datetime.strftime('%H:%M')}"
                            room = c.location.split(",")[0]
                            diff = (c.start_datetime.date() - now.date()).days
                            countdown = "Hôm nay" if diff == 0 else (f"Còn {diff} ngày" if diff > 0 else "")
                            lines.append(f"• <b>{c.title}</b> ({countdown})")
                            lines.append(f"  📅 {day_str} | ⏰ {time_str} | 📍 {room}\n")
                        send_telegram_message(bot_token, chat_id, "\n".join(lines))

                elif cmd in ("/nghi", "/baonghi"):
                    # List today's classes with inline button to mark cancelled
                    now = datetime.now()
                    today = now.date()
                    yt = client.get_year_and_term()
                    curr_year = yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
                    curr_term = yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"
                    raw_sched = client.get_schedule_semester(curr_year, curr_term)
                    classes = parse_semester_schedule(raw_sched)

                    cancelled_set = load_cancellations()
                    today_classes = [c for c in classes if c.start_datetime.date() == today]

                    if not today_classes:
                        send_telegram_message(bot_token, chat_id, "🏖️ Hôm nay bạn không có buổi học nào trên lịch.")
                    else:
                        buttons = []
                        for c in today_classes:
                            if c.uid in cancelled_set:
                                buttons.append([{"text": f"✅ {c.title[:25]} (Đã báo nghỉ)", "callback_data": "none"}])
                            else:
                                buttons.append([{"text": f"❌ Báo nghỉ môn: {c.title[:25]}", "callback_data": f"cancel_class:{c.uid}"}])

                        reply_markup = {"inline_keyboard": buttons}
                        send_telegram_message(
                            bot_token,
                            chat_id,
                            "👇 Chọn môn học giảng viên báo nghỉ hôm nay:",
                            reply_markup=reply_markup
                        )

        except Exception:
            pass
