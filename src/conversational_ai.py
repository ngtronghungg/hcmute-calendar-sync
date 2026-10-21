"""
HCMUTE Telegram Bot Conversational AI & Natural Language Understanding (NLU) Engine
Provides fluent, natural Vietnamese conversation, intelligent intent detection,
and seamless Google Gemini AI integration.
"""

import os
import re
import json
import unicodedata
import requests
from datetime import datetime, date, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

# Standard Vietnam Timezone (UTC+7)
VIETNAM_TZ = timezone(timedelta(hours=7))

def get_vietnam_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(VIETNAM_TZ).replace(tzinfo=None)


def normalize_vietnamese(text: str) -> str:
    """
    Remove accents and convert to lowercase for robust fuzzy matching.
    e.g. 'Ngày mai tôi có học gì không?' -> 'ngay mai toi co hoc gi khong?'
    """
    if not text:
        return ""
    text = text.lower().strip()
    # Replace 'đ' and 'Đ'
    text = text.replace("đ", "d").replace("Đ", "d")
    # Normalize unicode
    nfkd_form = unicodedata.normalize("NFKD", text)
    only_ascii = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    # Remove excessive punctuation
    clean = re.sub(r"[^\w\s\d/:-]", " ", only_ascii)
    return re.sub(r"\s+", " ", clean).strip()


def parse_relative_date(text: str, ref_now: Optional[datetime] = None) -> Optional[Tuple[date, str]]:
    """
    Detect relative day expressions in natural Vietnamese:
    - 'hôm nay', 'nay', 'today' -> today
    - 'ngày mai', 'mai', 'tomorrow' -> tomorrow
    - 'ngày mốt', 'mốt', 'ngày kia' -> day after tomorrow
    - 'hôm qua', 'qua' -> yesterday
    - 'thứ 2', 'thứ hai', 'thứ ba', 'thứ tư', 'thứ năm', 'thứ sáu', 'thứ bảy', 'chủ nhật'
    - 'ngày dd/mm' (e.g. 25/10)
    Returns: (target_date, date_display_label) or None
    """
    now = ref_now or get_vietnam_now()
    today = now.date()
    norm = normalize_vietnamese(text)

    # 1. Direct keywords
    if any(k in norm for k in ["hom nay", "ngay nay", "bua nay", "hom nay hoc", "lich nay", "nay hoc"]):
        return today, "hôm nay"
    if any(k in norm for k in ["ngay mai", "sang mai", "chieu mai", "toi mai", "ngay mai hoc"]):
        return today + timedelta(days=1), "ngày mai"
    # Single word 'mai' with context
    if re.search(r"\bmai\b", norm) and not re.search(r"\bkhuyen mai\b", norm):
        return today + timedelta(days=1), "ngày mai"
    if any(k in norm for k in ["ngay mot", "ngay kia", "bua mot"]):
        return today + timedelta(days=2), "ngày mốt"
    if re.search(r"\bmot\b", norm) and any(w in norm for w in ["hoc", "thi", "lich"]):
        return today + timedelta(days=2), "ngày mốt"
    if any(k in norm for k in ["hom qua", "qua hoc"]):
        return today - timedelta(days=1), "hôm qua"

    # 2. Specific date pattern: dd/mm or dd-mm
    dm_match = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?", norm)
    if dm_match:
        try:
            d_val = int(dm_match.group(1))
            m_val = int(dm_match.group(2))
            y_val = int(dm_match.group(3)) if dm_match.group(3) else today.year
            dt = date(y_val, m_val, d_val)
            return dt, f"ngày {dt.strftime('%d/%m/%Y')}"
        except Exception:
            pass

    # 3. Weekday matching
    weekday_map = {
        0: ["thu 2", "thu hai", "t2"],
        1: ["thu 3", "thu ba", "t3"],
        2: ["thu 4", "thu tu", "t4"],
        3: ["thu 5", "thu nam", "t5"],
        4: ["thu 6", "thu sau", "t6"],
        5: ["thu 7", "thu bay", "t7"],
        6: ["chu nhat", "cn"],
    }
    weekday_names_vn = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

    for w_idx, patterns in weekday_map.items():
        if any(re.search(rf"\b{p}\b", norm) for p in patterns):
            # Calculate target date in current or next week
            current_w = today.weekday()
            diff = w_idx - current_w
            if "tuan sau" in norm or "tuan toi" in norm:
                diff += 7
            elif diff < 0:
                # If that weekday already passed this week and user asks about it, assume next week
                diff += 7
            target = today + timedelta(days=diff)
            return target, f"{weekday_names_vn[w_idx]} ({target.strftime('%d/%m')})"

    return None


def parse_week_query(text: str, ref_now: Optional[datetime] = None) -> Optional[Tuple[date, date, str]]:
    """
    Detect whole-week queries:
    - 'tuần sau', 'tuan sau', 'tuần tới', 'tuan toi', 'next week' -> Monday to Sunday next week
    - 'tuần này', 'tuan nay', 'this week' -> Monday to Sunday this week
    Returns: (start_monday, end_sunday, label) or None
    """
    now = ref_now or get_vietnam_now()
    ref_d = now.date()
    norm = normalize_vietnamese(text)

    curr_mon = ref_d - timedelta(days=ref_d.weekday())
    curr_sun = curr_mon + timedelta(days=6)

    if any(w in norm for w in ["tuan sau", "tuan toi", "next week", "tuan ke tiep", "tuan tiep theo"]):
        next_mon = curr_mon + timedelta(days=7)
        next_sun = next_mon + timedelta(days=6)
        return next_mon, next_sun, "tuần sau"
    elif any(w in norm for w in ["tuan nay", "this week", "trong tuan"]):
        return curr_mon, curr_sun, "tuần này"

    return None


def format_classes_for_week(
    start_d: date,
    end_d: date,
    week_label: str,
    all_events: list,
    cancelled_set: set,
    student_name: str = "bạn",
) -> str:
    """Format full week schedule with day-by-day groupings and intelligent future preview."""
    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    week_classes = []
    for e in all_events:
        if (getattr(e, "event_type", "") or "").upper() in ("CLASS", "TKB"):
            ev_date = e.start_datetime.date()
            if start_d <= ev_date <= end_d and e.uid not in cancelled_set:
                week_classes.append(e)

    week_classes.sort(key=lambda x: x.start_datetime)
    d_start_str = start_d.strftime("%d/%m")
    d_end_str = end_d.strftime("%d/%m/%Y")

    if week_classes:
        lines = [
            f"📅 <b>LỊCH HỌC {week_label.upper()} ({d_start_str} - {d_end_str}):</b>\n"
        ]
        curr_day = None
        for c in week_classes:
            ev_d = c.start_datetime.date()
            if ev_d != curr_day:
                curr_day = ev_d
                w_name = weekday_names[ev_d.weekday()]
                lines.append(f"\n📌 <b>{w_name}, ngày {ev_d.strftime('%d/%m')}:</b>")
            t_str = f"{c.start_datetime.strftime('%H:%M')} - {c.end_datetime.strftime('%H:%M')}"
            r_str = c.location.split(",")[0] if c.location else "Xem TKB"
            title_clean = c.title.replace("[HỌC BÙ] ", "")
            lines.append(f" • ⏰ {t_str} | 📍 <b>{r_str}</b>\n   📚 {title_clean}")
        return "\n".join(lines)
    else:
        # No classes this week: preview next upcoming classes
        future_classes = [
            e for e in all_events
            if (getattr(e, "event_type", "") or "").upper() in ("CLASS", "TKB")
            and e.start_datetime.date() > end_d
            and e.uid not in cancelled_set
        ]
        future_classes.sort(key=lambda x: x.start_datetime)

        lines = [
            f"🏖️ <b>Tuyệt vời bạn {student_name}!</b>",
            f"Vào <b>{week_label} ({d_start_str} - {d_end_str})</b>, bạn hoàn toàn <b>không có lịch học trên lớp</b>! 🎉",
            "\nBạn có thể tranh thủ nghỉ ngơi, tự ôn tập hoặc thư giãn nha! ☕✨"
        ]

        if future_classes:
            lines.append(f"\n📅 <b>Lịch học gần nhất tiếp theo của bạn:</b>")
            grouped = {}
            for fc in future_classes[:6]:
                f_d = fc.start_datetime.date()
                if f_d not in grouped:
                    grouped[f_d] = []
                grouped[f_d].append(fc)
            for f_d, items in list(grouped.items())[:2]:
                w_name = weekday_names[f_d.weekday()]
                lines.append(f" • <b>{w_name}, {f_d.strftime('%d/%m/%Y')}:</b>")
                for it in items:
                    t_str = f"{it.start_datetime.strftime('%H:%M')} - {it.end_datetime.strftime('%H:%M')}"
                    r_str = it.location.split(",")[0] if it.location else "Xem TKB"
                    lines.append(f"   ⏰ {t_str} | 📍 <b>{r_str}</b> | {it.title}")

        return "\n".join(lines)


def extract_course_search_keyword(text: str) -> Optional[str]:
    """
    Extract course title from queries like:
    - 'môn triết học ở đâu' -> 'triết'
    - 'học môn toán cao cấp khi nào' -> 'toán cao cấp'
    - 'phòng học tiếng anh' -> 'tiếng anh'
    - 'tìm môn ctm' -> 'ctm'
    - 'môn kỹ thuật thủy lực khí nén học phòng nào' -> 'kỹ thuật thủy lực khí nén'
    """
    norm = normalize_vietnamese(text)
    # If the user is specifically asking about an exam, don't hijack it as a general course search
    if any(w in norm for w in [
        "mon thi", "thi ke tiep", "thi tiep theo", "mon sap thi",
        "khi nao thi", "lich thi", "bao gio thi", "ca thi", "phong thi"
    ]):
        return None

    clean = text.strip()
    patterns = [
        r"(?:môn|mon|học phần|hoc phan|lớp|lop|môn học|mon hoc)\s+([a-zA-Z0-9\s_À-ỹ\-\+]{2,40})",
        r"(?:phòng|phong|giờ|gio|thời gian|thoi gian)\s+(?:học\s+|hoc\s+)?(?:môn\s+|mon\s+)?([a-zA-Z0-9\s_À-ỹ\-\+]{2,40})",
        r"(?:tìm|tim|kiếm|kiem|tra cứu|tra cuu)\s+(?:môn\s+|mon\s+)?([a-zA-Z0-9\s_À-ỹ\-\+]{2,40})",
    ]
    for p in patterns:
        m = re.search(p, clean, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            # 1. Strip trailing question phrases
            candidate = re.sub(
                r"(?i)\s+(?:ở đâu|o dau|ở phòng nào|o phong nao|phòng nào|phong nao|phòng mấy|phong may|khi nào|khi nao|mấy giờ|may gio|như thế nào|nhu the nao|là gì|la gi|thế nào|the nao|mấy|may|nào|nao)\??$",
                "", candidate
            ).strip()
            # 2. Strip trailing action/location words
            candidate = re.sub(r"(?i)\s+(?:học|hoc|phòng|phong|lớp|lop|giờ|gio)\??$", "", candidate).strip()
            candidate_norm = normalize_vietnamese(candidate)
            if len(candidate) >= 2 and candidate_norm not in ["gi", "hoc", "thi", "nghi", "hom nay", "mai", "mot", "nay", "ke tiep", "tiep theo"]:
                return candidate
    return None


def format_classes_for_date(target_date: date, date_label: str, all_events: list, cancelled_set: set, student_name: str = "bạn") -> str:
    """Format daily class schedule with warm, natural Vietnamese tone."""
    weekday_names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    day_name = weekday_names[target_date.weekday()]
    date_str = target_date.strftime("%d/%m/%Y")

    day_classes = [
        c for c in all_events
        if (getattr(c, "event_type", "") or "").upper() in ("CLASS", "TKB")
        and c.start_datetime.date() == target_date
        and c.uid not in cancelled_set
    ]
    day_classes.sort(key=lambda x: x.start_datetime)

    cancelled_classes = [
        c for c in all_events
        if (getattr(c, "event_type", "") or "").upper() in ("CLASS", "TKB")
        and c.start_datetime.date() == target_date
        and c.uid in cancelled_set
    ]

    exams_on_day = [
        e for e in all_events
        if ((getattr(e, "event_type", "") or "").upper() in ("EXAM", "THI") or "THI" in (e.title or "").upper())
        and e.start_datetime.date() == target_date
    ]

    lines = [
        f"📅 <b>LỊCH CỦA BẠN VÀO {date_label.upper()} ({day_name}, {date_str}):</b>\n",
    ]

    if exams_on_day:
        lines.append("🔥 <b>CẢNH BÁO LỊCH THI HÔM NÀY:</b>")
        for ex in exams_on_day:
            lines.append(f"• 🎯 <b>{ex.title}</b>")
            lines.append(f"  ⏰ Giờ thi: <b>{ex.start_datetime.strftime('%H:%M')}</b>")
            lines.append(f"  📍 Phòng thi: <b>{ex.location or 'Xem cổng trường'}</b>\n")

    if day_classes:
        lines.append(f"📚 <b>Buổi học trên lớp ({len(day_classes)} môn):</b>")
        for idx, c in enumerate(day_classes, 1):
            time_str = f"{c.start_datetime.strftime('%H:%M')} - {c.end_datetime.strftime('%H:%M')}"
            room = c.location.split(",")[0] if c.location else "Xem TKB"
            is_makeup = "[HỌC BÙ]" in (c.title or "")
            badge = " <i>[Lịch học bù]</i>" if is_makeup else ""
            lines.append(f"<b>{idx}. {c.title}</b>{badge}")
            lines.append(f"   🕒 Giờ: <b>{time_str}</b> | 📍 Phòng: <b>{room}</b>")
            if c.description and "GV:" in c.description:
                gv_match = re.search(r"GV:\s*([^,\n]+)", c.description)
                if gv_match:
                    lines.append(f"   👨‍🏫 Giảng viên: {gv_match.group(1).strip()}")
            lines.append("")
        lines.append("💡 <i>Chúc bạn một ngày học tập nhiều hứng khởi và tiếp thu thật tốt kiến thức nhé! ✨</i>")
    elif not exams_on_day:
        lines.append(f"🏖️ <b>Tuyệt vời! Vào {date_label} ({day_name}), bạn hoàn toàn không có lịch học trên lớp!</b>")
        lines.append("\nBạn có thể tranh thủ ngủ nướng một chút, tự ôn lại bài vở hoặc đi cà phê thư giãn nha! ☕✨")

    if cancelled_classes:
        lines.append("\n🏖️ <i>Lưu ý: Có môn đã được Giảng viên cho nghỉ:</i>")
        for cc in cancelled_classes:
            lines.append(f" • 🏖️ {cc.title} (Đã xóa khỏi lịch)")

    return "\n".join(lines)


def format_next_exam(client: Any = None, chat_id: Optional[str] = None) -> str:
    """Format specifically the upcoming exam closest to current date and time."""
    from src.telegram_bot import load_feed_cache, load_cached_exams, get_student_identity
    all_events = load_feed_cache()
    exams = [
        e for e in all_events
        if (getattr(e, "event_type", "") or "").upper() in ("EXAM", "THI") or "THI" in (e.title or "").upper()
    ]
    if not exams:
        exams = load_cached_exams()

    now = get_vietnam_now()
    upcoming = [e for e in exams if e.end_datetime >= now]
    upcoming.sort(key=lambda x: x.start_datetime)

    student_name, student_mssv = get_student_identity(chat_id, client)

    if not upcoming:
        if exams:
            exams.sort(key=lambda x: x.start_datetime, reverse=True)
            last_ex = exams[0]
            return (
                f"🎉 <b>Chúc mừng {student_name}!</b>\n\n"
                f"Bạn không còn môn thi nào sắp tới trong kỳ này (Môn gần nhất là <b>{last_ex.title}</b> đã thi xong vào ngày {last_ex.start_datetime.strftime('%d/%m/%Y')}).\n"
                "Chúc bạn có một kỳ nghỉ thật vui vẻ hoặc chuẩn bị tốt cho học kỳ tiếp theo nha! 🏖️🍹"
            )
        return "📝 <b>Hiện tại trường chưa công bố lịch thi kết thúc học phần cho bạn!</b>"

    next_ex = upcoming[0]
    diff_days = (next_ex.start_datetime.date() - now.date()).days
    if diff_days == 0:
        time_badge = "🔥 <b>HÔM NAY</b>"
    elif diff_days == 1:
        time_badge = "⚡ <b>NGÀY MAI</b>"
    else:
        time_badge = f"⏳ <b>Còn {diff_days} ngày nữa</b>"

    time_str = next_ex.start_datetime.strftime("%H:%M ngày %d/%m/%Y")
    room_str = next_ex.location or "Xem cổng trường online.hcmute.edu.vn"

    lines = [
        f"🎯 <b>MÔN THI KẾ TIẾP CỦA BẠN:</b>\n",
        f"📚 Môn thi: <b>{next_ex.title}</b>",
        f"⏰ Thời gian: <b>{time_str}</b> ({time_badge})",
        f"📍 Phòng thi: <b>{room_str}</b>\n",
    ]

    if len(upcoming) > 1:
        lines.append(f"<i>(Sau môn này bạn còn {len(upcoming) - 1} môn thi nữa. Bấm '📝 Lịch thi' để xem toàn bộ danh sách nhé!)</i>")
    else:
        lines.append("<i>Đây là môn thi cuối cùng trong kỳ này của bạn rồi đó! Chúc bạn thi thật tốt và đạt điểm cao nha! 💪🎓</i>")

    return "\n".join(lines)


def search_and_format_course(keyword: str, all_events: list, cancelled_set: set) -> Optional[str]:
    """Search for a specific subject by name or code and return full schedule."""
    norm_kw = normalize_vietnamese(keyword)
    if not norm_kw:
        return None

    # HCMUTE student common acronyms
    acronyms = {
        "ctm": "cong nghe che tao may",
        "gdtc": "giao duc the chat",
        "ttnt": "tri tue nhan tao",
        "ktlt": "ky thuat lap trinh",
        "shcd": "sinh hoat cong dan",
        "dstt": "dai so tuyen tinh",
        "gtdc": "giai tich dai cuong",
    }
    for acr, expansion in acronyms.items():
        if acr == norm_kw or f" {acr} " in f" {norm_kw} ":
            norm_kw = re.sub(rf"\b{acr}\b", expansion, norm_kw).strip()

    stopwords = {"mon", "hoc", "lop", "o", "dau", "phong", "nao", "khi", "gio", "la", "gi", "phan", "tim"}
    kw_words = [w for w in re.findall(r"\w+", norm_kw) if w not in stopwords]
    if not kw_words:
        kw_words = [w for w in re.findall(r"\w+", norm_kw)]
    if not kw_words:
        return None

    matched_events = []
    for ev in all_events:
        if (getattr(ev, "event_type", "") or "").upper() in ("CLASS", "TKB", "EXAM", "THI"):
            title_norm = normalize_vietnamese(ev.title)
            desc_norm = normalize_vietnamese(ev.description or "")
            loc_norm = normalize_vietnamese(ev.location or "")

            # 1. Exact substring match
            if norm_kw in title_norm or norm_kw in desc_norm:
                matched_events.append((ev, 1.0))
                continue

            # 2. Token overlap fuzzy match (handles hyphens, brackets, word order)
            all_text_words = set(re.findall(r"\w+", f"{title_norm} {desc_norm}"))
            matches = sum(1 for w in kw_words if w in all_text_words)
            overlap_score = matches / len(kw_words)
            if overlap_score >= 0.6:
                matched_events.append((ev, overlap_score))

    if not matched_events:
        return None

    # Group and pick top title
    matched_events.sort(key=lambda x: (x[1], -x[0].start_datetime.timestamp()), reverse=True)
    top_title = matched_events[0][0].title
    top_events = [ev for ev, score in matched_events if ev.title == top_title]
    top_events.sort(key=lambda x: x.start_datetime)

    now = get_vietnam_now()
    upcoming = [e for e in top_events if e.end_datetime >= now and e.uid not in cancelled_set]

    sample = top_events[0]
    title_display = sample.title.replace("[HỌC BÙ] ", "")
    room_display = sample.location.split(",")[0] if sample.location else "Xem TKB"

    lines = [
        f"🔍 <b>KẾT QUẢ TÌM KIẾM MÔN HỌC:</b>",
        f"📚 Môn: <b>{title_display}</b>",
        f"📍 Phòng thường học: <b>{room_display}</b>\n",
    ]

    if upcoming:
        lines.append(f"📅 <b>Các buổi học sắp tới ({len(upcoming)} buổi):</b>")
        weekday_names = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        for c in upcoming[:5]:
            w_str = weekday_names[c.start_datetime.weekday()]
            d_str = c.start_datetime.strftime("%d/%m/%Y")
            t_str = f"{c.start_datetime.strftime('%H:%M')} - {c.end_datetime.strftime('%H:%M')}"
            r_str = c.location.split(",")[0] if c.location else room_display
            diff = (c.start_datetime.date() - now.date()).days
            badge = "Hôm nay" if diff == 0 else ("Ngày mai" if diff == 1 else f"Còn {diff} ngày")
            lines.append(f" • <b>{w_str}, {d_str}</b> ({badge}) | ⏰ {t_str} | 📍 {r_str}")
    else:
        lines.append("🏖️ <i>Môn này hiện không còn buổi học nào trong các tuần tới (hoặc đã kết thúc học phần).</i>")

    return "\n".join(lines)


def format_telegram_html(text: str) -> str:
    """Sanitize and convert standard markdown to Telegram-supported HTML tags."""
    if not text:
        return ""
    # Strip any leaked debug artifacts like "Response details:"
    text = re.sub(r"(?i)\**response details:?\**.*", "", text, flags=re.DOTALL).strip()
    # Convert code blocks
    text = re.sub(r"```([a-zA-Z0-9_]*)\n([\s\S]*?)```", r"<pre>\2</pre>", text)
    # Convert inline code
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    # Convert bold (both multiline and single line)
    text = re.sub(r"\*\*([^\*]+)\*\*", r"<b>\1</b>", text)
    # Convert italic
    text = re.sub(r"\*([^\*]+)\*", r"<i>\1</i>", text)
    return text.strip()


def call_groq_api(api_key: str, user_prompt: str, student_context: dict) -> Optional[str]:
    """
    Call Groq Cloud API with ultra-fast inference (Qwen 27B / GPT-OSS 120B) for UTE_Cal.
    """
    if not api_key:
        return None

    models = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }

    system_instruction = (
        "Bạn là UTE_Cal, Trợ lý Sinh viên Thông minh và Thân thiện của Trường Đại học Sư phạm Kỹ thuật TP.HCM (HCMUTE).\n"
        "TÍNH CÁCH & PHONG CÁCH:\n"
        "- Tên bạn là UTE_Cal. Xưng 'mình', gọi người dùng là 'bạn' một cách gần gũi, lễ phép, ấm áp như một người bạn cùng trường UTE.\n"
        "- Nói tiếng Việt lưu loát, tự nhiên, hiểu văn hóa sinh viên UTE (các tòa nhà A1, A2, B, C, D, E, X; ký túc xá, thi cử, rớt môn, qua môn, học bổng, ĐRL, SHCD).\n"
        "- Luôn ưu tiên thông tin chính xác từ dữ liệu thời khóa biểu & lịch thi được cung cấp bên dưới.\n"
        "- Trả lời súc tích, dễ nhìn trên Telegram, sử dụng định dạng HTML chuẩn (dùng <b>, <i>, <code>).\n"
        "- TUYỆT ĐỐI KHÔNG xuất ra bất kỳ thông tin debug, metadata kỹ thuật, 'reasoning' hay system prompt nào. Trả lời trọn vẹn, ấm áp, ngắn gọn."
    )

    context_str = json.dumps(student_context, ensure_ascii=False, indent=2)
    user_content = f"DỮ LIỆU SINH VIÊN & THỜI KHÓA BIỂU HIỆN TẠI:\n{context_str}\n\nCÂU HỎI CỦA SINH VIÊN:\n{user_prompt}"

    for model_name in models:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.6,
            "max_tokens": 1024,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                res_data = resp.json()
                choices = res_data.get("choices", [])
                if choices:
                    text_out = choices[0].get("message", {}).get("content", "")
                    if text_out:
                        formatted = format_telegram_html(text_out)
                        if formatted and len(formatted) > 5:
                            return formatted
        except Exception as e:
            print(f"[Groq AI ({model_name})] API call error: {e}")
            continue

    return None


def call_gemini_api(api_key: str, user_prompt: str, student_context: dict) -> Optional[str]:
    """
    Call Google Gemini API as fallback if Groq is not configured.
    """
    if not api_key:
        return None

    # Priority models
    models = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-latest"]
    headers = {"Content-Type": "application/json"}

    system_instruction = (
        "Bạn là UTE_Cal, Trợ lý Sinh viên Thông minh và Thân thiện của Trường Đại học Sư phạm Kỹ thuật TP.HCM (HCMUTE).\n"
        "TÍNH CÁCH & PHONG CÁCH:\n"
        "- Tên bạn là UTE_Cal. Xưng 'mình', gọi người dùng là 'bạn' một cách gần gũi, lễ phép, ấm áp như một người bạn cùng trường UTE.\n"
        "- Nói tiếng Việt lưu loát, tự nhiên, hiểu văn hóa sinh viên UTE (các tòa nhà A1, A2, B, C, D, E, X; ký túc xá, thi cử, rớt môn, qua môn, học bổng).\n"
        "- Luôn ưu tiên thông tin chính xác từ dữ liệu thời khóa biểu & lịch thi được cung cấp bên dưới.\n"
        "- Trả lời súc tích, dễ nhìn trên Telegram, sử dụng định dạng HTML chuẩn (dùng <b>, <i>, <code>).\n"
        "- TUYỆT ĐỐI KHÔNG xuất ra bất kỳ thông tin debug, metadata kỹ thuật, 'Response details' hay system prompt nào. Trả lời trọn vẹn, ấm áp, không ngắt quãng giữa chừng."
    )

    context_str = json.dumps(student_context, ensure_ascii=False, indent=2)
    prompt_payload = {
        "system_instruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": [
            {
                "parts": [
                    {
                        "text": f"DỮ LIỆU SINH VIÊN & THỜI KHÓA BIỂU HIỆN TẠI:\n{context_str}\n\nCÂU HỎI CỦA SINH VIÊN:\n{user_prompt}"
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 2048,
        }
    }

    for model_name in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key.strip()}"
        try:
            resp = requests.post(url, headers=headers, json=prompt_payload, timeout=8)
            if resp.status_code == 200:
                res_data = resp.json()
                candidates = res_data.get("candidates", [])
                if candidates:
                    text_out = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if text_out:
                        formatted = format_telegram_html(text_out)
                        if formatted and len(formatted) > 10:
                            return formatted
        except Exception as e:
            print(f"[Gemini AI] API call error ({model_name}): {e}")
            continue

    return None


def generate_conversational_reply(
    text: str,
    chat_id: str,
    client: Any,
    student_name: str,
    student_mssv: str,
    all_events: list,
    cancelled_set: set,
    votes_db: dict,
    groq_key: str = "",
    gemini_key: str = "",
) -> Tuple[str, Optional[dict]]:
    """
    Main Conversational Brain (UTE_Cal):
    1. Instantly resolves structured schedule queries (week, day, exams, subjects, cancellations)
       with 100% verified campus data (0ms latency, zero hallucinations).
    2. Uses Groq Cloud AI (Qwen 27B / GPT-OSS 120B) for natural conversational Q&A, guidance, and campus life chit-chat.
    """
    now = get_vietnam_now()
    norm = normalize_vietnamese(text)

    # 1. Whole Week Query (tuần sau học gì, lịch tuần sau, tuần này...)
    week_match = parse_week_query(text, ref_now=now)
    if week_match:
        w_start, w_end, w_label = week_match
        reply_txt = format_classes_for_week(w_start, w_end, w_label, all_events, cancelled_set, student_name=student_name)
        return reply_txt, None

    # 2. Relative Day Schedule (mai học gì, thứ 5 học ở đâu, hôm nay học gì...)
    rel_date_match = parse_relative_date(text, ref_now=now)
    if rel_date_match:
        target_d, label_d = rel_date_match
        reply_txt = format_classes_for_date(target_d, label_d, all_events, cancelled_set, student_name=student_name)
        return reply_txt, None

    # 3. Exam Schedule & Next Exam (môn thi kế tiếp là gì, khi nào thi, lịch thi, môn sắp thi...)
    is_exam_query = any(w in norm for w in [
        "lich thi", "khi nao thi", "bao gio thi", "thi cu", "phong thi", "ca thi",
        "co lich thi chua", "mon thi", "thi ke tiep", "thi tiep theo", "mon sap thi"
    ])
    if is_exam_query:
        is_next_exam = any(w in norm for w in ["ke tiep", "tiep theo", "sap thi", "gan nhat", "toi day", "tiep"])
        if is_next_exam or "mon thi" in norm:
            return format_next_exam(client, chat_id=chat_id), None
        from src.telegram_bot import format_upcoming_exams
        return format_upcoming_exams(client, chat_id=chat_id), None

    # 4. Specific Subject Search (môn Triết, môn CTM, môn kỹ thuật thủy lực khí nén học phòng nào...)
    course_kw = extract_course_search_keyword(text)
    if course_kw:
        res_course = search_and_format_course(course_kw, all_events, cancelled_set)
        if res_course:
            return res_course, None

    # 5. Common Subject Keyword fallback (ctm, triet, toan, ly, hoa, the duc...)
    common_subjects = ["triet", "toan", "ly", "hoa", "anh", "tieng anh", "ctm", "the duc", "gdtc", "lap trinh", "chuyen nganh", "do an"]
    for s in common_subjects:
        if s in norm.split():
            res_course = search_and_format_course(s, all_events, cancelled_set)
            if res_course:
                return res_course, None

    # 6. Cancellation questions (có được nghỉ không, môn nào nghỉ, báo nghỉ...)
    if any(w in norm for w in ["duoc nghi khong", "co nghi khong", "bao nghi", "nghi hoc", "co mon nao nghi", "thay co bao nghi"]):
        cancelled_list = [e for e in all_events if e.uid in cancelled_set and e.end_datetime >= now]
        if cancelled_list:
            lines = [f"🏖️ <b>HIỆN TẠI CÓ {len(cancelled_list)} BUỔI HỌC GIẢNG VIÊN CHO NGHỈ:</b>\n"]
            for c in cancelled_list[:5]:
                dt_str = c.start_datetime.strftime("%d/%m/%Y lúc %H:%M")
                lines.append(f"• 🏖️ <b>{c.title}</b> ({dt_str})")
            lines.append("\n✔ Các buổi học này đã được xóa khỏi Lịch của bạn rồi nha!")
            return "\n".join(lines), None
        else:
            return "✔ <b>Hiện tại không có môn nào giảng viên cho nghỉ!</b>\nBạn đi học đúng giờ và nhớ chú ý an toàn trên đường đến trường nha! 🛵🏫", None

    # 7. Tuition & Deadlines (học phí, tiền học, deadline...)
    if any(w in norm for w in ["hoc phi", "tien hoc", "dong tien", "deadline", "han chot", "shcd", "tin chi"]):
        from src.telegram_bot import format_reminders
        return format_reminders(client), None

    # 8. Bot Identity / Hướng dẫn chức năng
    if any(w in norm for w in ["ban la ai", "may la ai", "bot la ai", "ten gi", "gioi thieu", "chuc nang", "huong dan"]):
        intro = (
            f"🤖 <b>Chào bạn, mình là UTE_Cal!</b>\n\n"
            "Mình là Trợ lý Thời khóa biểu & Đồng hành Sinh viên của <b>Đại học Sư phạm Kỹ thuật TP.HCM (HCMUTE)</b> với các khả năng tự động:\n"
            "• ☀️ <b>Nhắc lịch mỗi sáng:</b> 06:30 sáng tự động gửi môn học & phòng học trong ngày.\n"
            "• 📅 <b>Tra cứu lịch siêu nhanh:</b> Bạn chỉ cần hỏi tự nhiên <i>'mai học gì', 'thứ 5 học ở đâu', 'môn Triết học phòng nào'</i>...\n"
            "• 🔥 <b>Đếm ngược lịch thi:</b> Cảnh báo phòng thi, giờ thi cận kề.\n"
            "• 🏖️ <b>Đánh dấu GV cho nghỉ:</b> Tự động xóa khỏi Lịch khi thầy/cô thông báo cho nghỉ.\n"
            "• 💡 <b>Trò chuyện thông minh cùng Groq AI:</b> Giải đáp thắc mắc và tâm sự sinh viên.\n\n"
            "Bạn thử nhắn <i>'mai học gì'</i> hoặc <i>'môn thi kế tiếp là gì'</i> xem sao nha! 😉"
        )
        return intro, None

    # 9. Greetings / Chào hỏi
    if any(norm == w or norm.startswith(w + " ") for w in ["chao", "hi", "hello", "alo", "helo", "hi bot", "chao bot", "chao ban", "chao em"]):
        greeting = (
            f"👋 <b>Chào {student_name}! Mình là UTE_Cal, rất vui được hỗ trợ bạn!</b> 😊\n\n"
            f"Mình là Trợ lý Thời khóa biểu & Lịch thi HCMUTE. Bạn muốn xem lịch học hôm nay, ngày mai, tuần sau, tra cứu phòng học hay kiểm tra lịch thi cứ hỏi tự nhiên nha!"
        )
        return greeting, None

    # 10. Thanks / Cảm ơn
    if any(w in norm for w in ["cam on", "thank", "tks", "cam on nha", "tuyet voi", "bot gioi qua", "ok bot", "da ro", "tot lam"]):
        replies = (
            f"Dạ không có chi nè {student_name}! 🥰\n\n"
            "Cần kiểm tra phòng học, giờ thi hay báo nghỉ môn nào cứ ới mình bất kỳ lúc nào nhé. Chúc bạn một kỳ học thật bùng nổ và đạt GPA thật cao nha! 🎓✨"
        )
        return replies, None

    # 11. Encouragement & Chit-chat (mệt quá, buồn quá, thi rớt, khó quá...)
    if any(w in norm for w in ["met qua", "buon qua", "kho qua", "chan qua", "thi rot", "stress", "ap luc"]):
        replies = (
            f"Thương bạn {student_name} nhiều nè! 🥺 Cố lên nha!\n\n"
            "Học ở HCMUTE bài vở và đồ án đôi khi hơi dồn dập, nhưng bạn đã đi được một chặng đường rất xa rồi đó! "
            "Uống một ly trà sữa hoặc đi dạo quanh tòa A, hít thở một chút rồi tiếp tục chiến đấu nha! UTE_Cal luôn ở đây đồng hành cùng bạn! 💪🔥"
        )
        return replies, None

    # 12. Try Groq AI (or Gemini fallback) for open-ended conversation & campus questions
    ai_key = groq_key or gemini_key
    if ai_key:
        upcoming_events_summary = []
        for e in all_events[:25]:
            if e.end_datetime >= now - timedelta(days=1) and e.uid not in cancelled_set:
                upcoming_events_summary.append({
                    "title": e.title,
                    "type": getattr(e, "event_type", "CLASS"),
                    "start": e.start_datetime.strftime("%Y-%m-%d %H:%M"),
                    "end": e.end_datetime.strftime("%Y-%m-%d %H:%M"),
                    "location": e.location,
                })

        context_dict = {
            "student_name": student_name,
            "student_mssv": student_mssv,
            "current_vietnam_time": now.strftime("%Y-%m-%d %H:%M:%S (Thứ %w)"),
            "today_date": now.strftime("%d/%m/%Y"),
            "schedule": upcoming_events_summary,
            "cancelled_count": len(cancelled_set),
        }
        if groq_key or ai_key.startswith("gsk_"):
            ai_reply = call_groq_api(ai_key, text, context_dict)
        else:
            ai_reply = call_gemini_api(ai_key, text, context_dict)
            if not ai_reply:
                ai_reply = call_groq_api(ai_key, text, context_dict)

        if ai_reply:
            return ai_reply, None

    # 13. Default Conversational Fallback
    default_text = (
        f"🤔 Mình chưa hiểu rõ yêu cầu này của bạn nè {student_name}!\n\n"
        "Bạn có thể nhắn cho mình như các câu sau:\n"
        "• <i>'Mai tui học gì ở phòng nào?'</i>\n"
        "• <i>'Môn thi kế tiếp là gì?'</i>\n"
        "• <i>'Môn kỹ thuật thủy lực khí nén học phòng nào?'</i>\n"
        "• <i>'Tuần sau có môn nào không?'</i>\n\n"
        "Hoặc bạn bấm nhanh các nút bên dưới để xem lịch tức thì nhé! 👇"
    )
    return default_text, None
