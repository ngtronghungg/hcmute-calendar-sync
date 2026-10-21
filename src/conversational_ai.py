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


def extract_course_search_keyword(text: str) -> Optional[str]:
    """
    Extract course title from queries like:
    - 'môn triết học ở đâu' -> 'triết'
    - 'học môn toán cao cấp khi nào' -> 'toán cao cấp'
    - 'phòng học tiếng anh' -> 'tiếng anh'
    - 'tìm môn ctm' -> 'ctm'
    """
    clean = text.strip()
    patterns = [
        r"(?:môn|mon|học phần|hoc phan|lớp|lop|môn học|mon hoc)\s+([a-zA-Z0-9\s_À-ỹ\-\+]{2,30})",
        r"(?:phòng|phong|giờ|gio|thời gian|thoi gian)\s+(?:học\s+|hoc\s+)?(?:môn\s+|mon\s+)?([a-zA-Z0-9\s_À-ỹ\-\+]{2,30})",
        r"(?:tìm|tim|kiếm|kiem|tra cứu|tra cuu)\s+(?:môn\s+|mon\s+)?([a-zA-Z0-9\s_À-ỹ\-\+]{2,30})",
    ]
    for p in patterns:
        m = re.search(p, clean, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            # remove common trailing question words
            candidate = re.sub(r"\s+(?:học|hoc)?\s*(?:ở đâu|o dau|ở phòng nào|o phong nao|khi nào|khi nao|mấy giờ|may gio|như thế nào|nhu the nao|phòng mấy|phong may|nào|nao|thế nào|the nao)\??$", "", candidate, flags=re.IGNORECASE).strip()
            candidate_norm = normalize_vietnamese(candidate)
            if len(candidate) >= 2 and candidate_norm not in ["gi", "hoc", "thi", "nghi", "hom nay", "mai", "mot", "nay"]:
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
        lines.append("\n⚠️ <i>Lưu ý: Có môn đã được giảng viên / sinh viên báo nghỉ:</i>")
        for cc in cancelled_classes:
            lines.append(f" • ❌ {cc.title} (Đã xóa khỏi lịch)")

    return "\n".join(lines)


def search_and_format_course(keyword: str, all_events: list, cancelled_set: set) -> Optional[str]:
    """Search for a specific subject by name or code and return full schedule."""
    norm_kw = normalize_vietnamese(keyword)
    if not norm_kw:
        return None

    matched_events = []
    for ev in all_events:
        if (getattr(ev, "event_type", "") or "").upper() in ("CLASS", "TKB", "EXAM", "THI"):
            title_norm = normalize_vietnamese(ev.title)
            desc_norm = normalize_vietnamese(ev.description or "")
            loc_norm = normalize_vietnamese(ev.location or "")
            if norm_kw in title_norm or norm_kw in desc_norm or norm_kw in loc_norm:
                matched_events.append(ev)

    if not matched_events:
        return None

    # Group by title
    matched_events.sort(key=lambda x: x.start_datetime)
    now = get_vietnam_now()
    upcoming = [e for e in matched_events if e.end_datetime >= now and e.uid not in cancelled_set]

    sample = matched_events[0]
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


def call_gemini_api(api_key: str, user_prompt: str, student_context: dict) -> Optional[str]:
    """
    Call Google Gemini 1.5 Flash / 2.0 Flash API to generate natural, conversational Vietnamese responses.
    """
    if not api_key:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key.strip()}"
    headers = {"Content-Type": "application/json"}

    system_instruction = (
        "Bạn là Trợ lý Sinh viên Thông minh và Thân thiện của Trường Đại học Sư phạm Kỹ thuật TP.HCM (HCMUTE).\n"
        "TÍNH CÁCH & PHONG CÁCH:\n"
        "- Xưng 'mình', gọi người dùng là 'bạn' một cách gần gũi, lễ phép, ấm áp như một người bạn cùng trường UTE.\n"
        "- Nói tiếng Việt lưu loát, tự nhiên, hiểu văn hóa sinh viên UTE (các tòa nhà A1, A2, B, C, D, E, X; ký túc xá, thi cử, rớt môn, qua môn, học bổng).\n"
        "- Luôn ưu tiên thông tin chính xác từ dữ liệu thời khóa biểu & lịch thi được cung cấp bên dưới.\n"
        "- Trả lời súc tích, dễ nhìn trên Telegram, sử dụng định dạng HTML chuẩn (dùng <b>, <i>, <code>). KHÔNG dùng markdown ** hay ## vì Telegram dùng HTML parse_mode."
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
            "temperature": 0.5,
            "maxOutputTokens": 600,
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=prompt_payload, timeout=7)
        if resp.status_code == 200:
            res_data = resp.json()
            candidates = res_data.get("candidates", [])
            if candidates:
                text_out = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                if text_out:
                    return text_out.strip()
    except Exception as e:
        print(f"[Gemini AI] API call error: {e}")
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
    gemini_key: str = "",
) -> Tuple[str, Optional[dict]]:
    """
    Main Conversational Brain:
    1. Tries Gemini AI if API key is provided and responsive.
    2. Falls back to advanced Vietnamese NLU Engine with exact schedule data.
    """
    now = get_vietnam_now()
    norm = normalize_vietnamese(text)

    # 1. Check if Gemini AI is available
    if gemini_key:
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
        ai_reply = call_gemini_api(gemini_key, text, context_dict)
        if ai_reply:
            return ai_reply, None

    # 2. Built-in Vietnamese NLU Engine

    # A. Greetings / Chào hỏi
    if any(norm == w or norm.startswith(w + " ") for w in ["chao", "hi", "hello", "alo", "helo", "hi bot", "chao bot", "chao ban", "chao em"]):
        greeting = (
            f"👋 <b>Chào {student_name}! Rất vui được gặp bạn!</b> 😊\n\n"
            f"Mình là Trợ lý Thời khóa biểu & Lịch thi HCMUTE. Bạn muốn xem lịch học hôm nay, ngày mai, tra cứu phòng học hay kiểm tra lịch thi cứ bảo mình nha!"
        )
        return greeting, None

    # B. Thanks / Cảm ơn
    if any(w in norm for w in ["cam on", "thank", "tks", "cam on nha", "tuyet voi", "bot gioi qua", "ok bot", "da ro", "tot lam"]):
        replies = (
            f"Dạ không có chi nè {student_name}! 🥰\n\n"
            "Cần kiểm tra phòng học, giờ thi hay báo nghỉ môn nào cứ ới mình bất kỳ lúc nào nhé. Chúc bạn một kỳ học thật bùng nổ và đạt GPA thật cao nha! 🎓✨"
        )
        return replies, None

    # C. Bot Identity / Bạn là ai
    if any(w in norm for w in ["ban la ai", "may la ai", "bot la ai", "ten gi", "gioi thieu", "chuc nang", "huong dan"]):
        intro = (
            f"🤖 <b>Chào bạn, mình là Trợ lý HCMUTE Calendar!</b>\n\n"
            "Mình được tạo ra để đồng hành cùng các bạn sinh viên <b>Đại học Sư phạm Kỹ thuật TP.HCM (HCMUTE)</b> với các khả năng tự động:\n"
            "• ☀️ <b>Nhắc lịch mỗi sáng:</b> 06:30 sáng tự động gửi môn học & phòng học trong ngày.\n"
            "• 📅 <b>Tra cứu lịch siêu nhanh:</b> Bạn chỉ cần hỏi tự nhiên <i>'mai học gì', 'thứ 5 học ở đâu', 'môn Triết học phòng nào'</i>...\n"
            "• 🔥 <b>Đếm ngược lịch thi:</b> Cảnh báo phòng thi, giờ thi cận kề.\n"
            "• ❌ <b>Báo nghỉ Zalo cộng đồng:</b> Tự động phát hiện khi thầy cô báo nghỉ để xóa khỏi Google Calendar.\n\n"
            "Bạn thử nhắn <i>'mai học gì'</i> xem sao nha! 😉"
        )
        return intro, None

    # D. Relative Date Schedule (mai học gì, thứ 5 học ở đâu, hôm nay học gì...)
    rel_date_match = parse_relative_date(text, ref_now=now)
    if rel_date_match:
        target_d, label_d = rel_date_match
        reply_txt = format_classes_for_date(target_d, label_d, all_events, cancelled_set, student_name=student_name)
        return reply_txt, None

    # E. Specific Subject Search (môn Triết, môn CTM, Toán cao cấp, Tiếng Anh...)
    course_kw = extract_course_search_keyword(text)
    if course_kw:
        res_course = search_and_format_course(course_kw, all_events, cancelled_set)
        if res_course:
            return res_course, None

    # Try simple subject keywords in text
    common_subjects = ["triet", "toan", "ly", "hoa", "anh", "tieng anh", "ctm", "the duc", "gdtc", "lap trinh", "co so", "chuyen nganh", "do an"]
    for s in common_subjects:
        if s in norm:
            res_course = search_and_format_course(s, all_events, cancelled_set)
            if res_course:
                return res_course, None

    # F. Exam Schedule (khi nào thi, lịch thi, bao giờ thi...)
    if any(w in norm for w in ["lich thi", "khi nao thi", "bao gio thi", "thi cu", "phong thi", "ca thi", "co lich thi chua"]):
        from src.telegram_bot import format_upcoming_exams
        return format_upcoming_exams(client, chat_id=chat_id), None

    # G. Cancellation questions (có được nghỉ không, môn nào nghỉ, báo nghỉ...)
    if any(w in norm for w in ["duoc nghi khong", "co nghi khong", "bao nghi", "nghi hoc", "co mon nao nghi", "thay co bao nghi"]):
        cancelled_list = [e for e in all_events if e.uid in cancelled_set and e.end_datetime >= now]
        if cancelled_list:
            lines = [f"⚠️ <b>HIỆN TẠI CÓ {len(cancelled_list)} BUỔI HỌC ĐÃ ĐƯỢC BÁO NGHỈ:</b>\n"]
            for c in cancelled_list[:5]:
                dt_str = c.start_datetime.strftime("%d/%m/%Y lúc %H:%M")
                lines.append(f"• ❌ <b>{c.title}</b> ({dt_str})")
            lines.append("\n✔ Các buổi học này đã được xóa khỏi Google Calendar của bạn rồi nha!")
            return "\n".join(lines), None
        else:
            return "✔ <b>Hiện tại không có buổi học nào bị báo nghỉ!</b>\nBạn đi học đúng giờ và nhớ chú ý an toàn trên đường đến trường nha! 🛵🏫", None

    # H. Tuition & Deadlines (học phí, tiền học, deadline...)
    if any(w in norm for w in ["hoc phi", "tien hoc", "dong tien", "deadline", "han chot", "shcd", "tin chi"]):
        from src.telegram_bot import format_reminders
        return format_reminders(client), None

    # I. Encouragement & Chit-chat (mệt quá, buồn quá, thi rớt, khó quá...)
    if any(w in norm for w in ["met qua", "buon qua", "kho qua", "chan qua", "thi rot", "stress", "ap luc"]):
        replies = (
            f"Thương bạn {student_name} nhiều nè! 🥺 Cố lên nha!\n\n"
            "Học ở HCMUTE bài vở và đồ án đôi khi hơi dồn dập, nhưng bạn đã đi được một chặng đường rất xa rồi đó! "
            "Uống một ly trà sữa hoặc đi dạo quanh tòa A, hít thở một chút rồi tiếp tục chiến đấu nha! Mình luôn ở đây nhắc lịch cho bạn, bạn không một mình đâu! 💪🔥"
        )
        return replies, None

    # J. Default Friendly Conversational Guidance
    default_text = (
        f"🤔 Mình nghe bạn nói rồi nè {student_name}!\n\n"
        "Bạn có thể trò chuyện tự nhiên với mình về lịch học và lịch thi, ví dụ như:\n"
        "• <i>'Mai tui có học gì hông?'</i>\n"
        "• <i>'Thứ 5 học môn gì ở phòng nào?'</i>\n"
        "• <i>'Khi nào thi môn Giải tích?'</i>\n"
        "• <i>'Hôm nay có môn nào được nghỉ không?'</i>\n\n"
        "Hoặc bạn bấm nhanh các nút bên dưới để mình hỗ trợ tức thì nhé! 👇"
    )
    return default_text, None
