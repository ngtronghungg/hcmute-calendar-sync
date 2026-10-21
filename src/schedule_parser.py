"""
HCMUTE Schedule Parser
Parses raw JSON from HCMUTE schedule endpoints into clean, standardized class session events.
"""

from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any, Optional
import re
from dateutil import parser as dt_parser


# Official HCMUTE Period Time Table (Khung giờ giảng dạy và học tập trực tiếp trên lớp HCMUTE)
PERIOD_TABLE: Dict[int, Dict[str, time]] = {
    1: {"start": time(7, 30), "end": time(8, 15)},
    2: {"start": time(8, 15), "end": time(9, 0)},
    3: {"start": time(9, 0), "end": time(9, 45)},
    4: {"start": time(10, 0), "end": time(10, 45)},
    5: {"start": time(10, 45), "end": time(11, 30)},
    6: {"start": time(11, 30), "end": time(12, 15)},
    7: {"start": time(12, 45), "end": time(13, 30)},
    8: {"start": time(13, 30), "end": time(14, 15)},
    9: {"start": time(14, 15), "end": time(15, 0)},
    10: {"start": time(15, 15), "end": time(16, 0)},
    11: {"start": time(16, 0), "end": time(16, 45)},
    12: {"start": time(16, 45), "end": time(17, 30)},
    13: {"start": time(18, 0), "end": time(18, 45)},
    14: {"start": time(18, 45), "end": time(19, 30)},
    15: {"start": time(19, 30), "end": time(20, 15)},
    16: {"start": time(20, 15), "end": time(21, 0)},
}

# Day of week mapping: HCMUTE uses DayOfWeek: 1=Mon .. 7=Sun, or Vietnamese text
DAY_MAP: Dict[Any, int] = {
    "thứ hai": 0, "thứ 2": 0, "monday": 0, "mon": 0, 2: 0,
    "thứ ba": 1, "thứ 3": 1, "tuesday": 1, "tue": 1, 3: 1,
    "thứ tư": 2, "thứ 4": 2, "wednesday": 2, "wed": 2, 4: 2,
    "thứ năm": 3, "thứ 5": 3, "thursday": 3, "thu": 3, 5: 3,
    "thứ sáu": 4, "thứ 6": 4, "friday": 4, "fri": 4, 6: 4,
    "thứ bảy": 5, "thứ 7": 5, "saturday": 5, "sat": 5, 7: 5,
    "chủ nhật": 6, "cn": 6, "sunday": 6, "sun": 6, 8: 6, 1: 0,
}


class CalendarEvent:
    """Standardized Calendar Event Model."""

    def __init__(
        self,
        uid: str,
        title: str,
        start_datetime: datetime,
        end_datetime: datetime,
        location: str = "",
        description: str = "",
        event_type: str = "CLASS",  # CLASS, EXAM, DEADLINE, REMINDER
        category: str = "ACADEMIC",
        is_all_day: bool = False,
    ):
        self.uid = uid
        self.title = title
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime
        self.location = location
        self.description = description
        self.event_type = event_type
        self.category = category
        self.is_all_day = is_all_day

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "start": self.start_datetime.isoformat(),
            "end": self.end_datetime.isoformat(),
            "location": self.location,
            "description": self.description,
            "event_type": self.event_type,
            "category": self.category,
            "is_all_day": self.is_all_day,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CalendarEvent":
        return cls(
            uid=d["uid"],
            title=d["title"],
            start_datetime=datetime.fromisoformat(d["start"]),
            end_datetime=datetime.fromisoformat(d["end"]),
            location=d.get("location", ""),
            description=d.get("description", ""),
            event_type=d.get("event_type", "CLASS"),
            category=d.get("category", "ACADEMIC"),
            is_all_day=d.get("is_all_day", False),
        )

    def __repr__(self) -> str:
        return f"<Event {self.title} at {self.start_datetime.strftime('%Y-%m-%d %H:%M')}>"


def parse_period_range(period_str: Any) -> tuple[int, int]:
    """
    Extract start and end periods from strings like:
    '1-3', '1 -> 3', '1,2,3', 'Tiết 7-9', '10', 1
    """
    if period_str is None:
        return 1, 3
    s = str(period_str).strip()
    digits = [int(m) for m in re.findall(r"\b\d+\b", s)]
    if not digits:
        return 1, 3
    if len(digits) == 1:
        return digits[0], digits[0]
    return min(digits), max(digits)


def parse_date_safely(date_val: Any) -> Optional[date]:
    """Parse string or datetime to date object."""
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        return date_val.date()
    if isinstance(date_val, date):
        return date_val

    s = str(date_val).strip()
    try:
        # Check if format is YYYY-MM-DD or YYYY/MM/DD
        if re.match(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}", s):
            dt = dt_parser.parse(s, dayfirst=False)
        else:
            dt = dt_parser.parse(s, dayfirst=True)
        return dt.date()
    except Exception:
        return None


def get_period_time(start_p: int, end_p: int) -> tuple[time, time]:
    """Get start and end time objects for period range."""
    start_p = max(1, min(start_p, 16))
    end_p = max(start_p, min(end_p, 16))

    start_t = PERIOD_TABLE.get(start_p, {}).get("start", time(7, 0))
    end_t = PERIOD_TABLE.get(end_p, {}).get("end", time(11, 0))
    return start_t, end_t


def parse_semester_schedule(data: Any) -> List[CalendarEvent]:
    """
    Parse result of DrawingStudentSchedule_Perior into individual class events.
    Handles semester start date, end date, day of week, and periods.
    """
    items = []
    if isinstance(data, dict):
        if "result" in data and isinstance(data["result"], list):
            items = data["result"]
        elif "data" in data and isinstance(data["data"], list):
            items = data["data"]
        else:
            for k, v in data.items():
                if isinstance(v, list):
                    items = v
                    break
    elif isinstance(data, list):
        items = data

    events: List[CalendarEvent] = []

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        # Skip unassigned classes (Chưa Xếp TKB)
        xep_tkb = str(item.get("XepTKB") or "").strip().lower()
        if "chưa xếp" in xep_tkb or "không xếp" in xep_tkb:
            continue

        subject_name = (
            item.get("TenHP")
            or item.get("ClassCourseName")
            or item.get("CurriculumName")
            or item.get("TenMonHoc")
            or "Môn học"
        ).strip()

        subject_code = (
            item.get("MaLHP")
            or item.get("ClassCourseID")
            or item.get("CurriculumID")
            or item.get("MaHP")
            or ""
        ).strip()

        lecturer = (
            item.get("HoTenGV")
            or item.get("ProfessorName")
            or item.get("GiangVien")
            or "Chưa cập nhật"
        ).strip()

        room = (
            item.get("Phong")
            or item.get("Classroom")
            or item.get("RoomID")
            or "Chưa xếp phòng"
        ).strip()

        campus = item.get("CampusName") or "HCMUTE"

        # Day of week
        raw_thu = str(item.get("Thu") or "").strip().lower()
        target_weekday = DAY_MAP.get(raw_thu)
        if target_weekday is None:
            day_of_week_num = item.get("DayOfWeek")
            if day_of_week_num is not None:
                try:
                    # In HCMUTE, DayOfWeek 1=Monday (0), 2=Tuesday (1), ..., 7=Sunday (6)
                    target_weekday = int(day_of_week_num) - 1
                except (ValueError, TypeError):
                    pass

        if target_weekday is None:
            # Fallback string matching
            for k, v in DAY_MAP.items():
                if str(k).lower() in raw_thu:
                    target_weekday = v
                    break

        if target_weekday is None:
            target_weekday = 0  # default Monday if unspecified

        # Periods: check PeriodID & NumberOfPeriods
        start_p = 1
        end_p = 3
        if item.get("PeriodID"):
            try:
                start_p = int(item.get("PeriodID"))
                num_p = int(item.get("NumberOfPeriods") or 3)
                end_p = start_p + num_p - 1
            except (ValueError, TypeError):
                raw_period = item.get("CaHoc") or item.get("Tiet") or item.get("TietHoc")
                start_p, end_p = parse_period_range(raw_period)
        else:
            raw_period = item.get("CaHoc") or item.get("Tiet") or item.get("TietHoc")
            start_p, end_p = parse_period_range(raw_period)

        start_t, end_t = get_period_time(start_p, end_p)

        # Start & End dates
        # Check if TuanHoc contains date range: e.g. "02/11/2026->28/12/2026)"
        tuan_raw = str(item.get("TuanHoc") or item.get("WeekSchedule") or "")
        m_range = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s*->\s*(\d{1,2}/\d{1,2}/\d{4})", tuan_raw)

        begin_d = None
        end_d = None
        if m_range:
            begin_d = parse_date_safely(m_range.group(1))
            end_d = parse_date_safely(m_range.group(2))

        if not begin_d:
            raw_begin = item.get("BeginDate") or item.get("TuNgay") or item.get("TimeBeginDate")
            begin_d = parse_date_safely(raw_begin)

        if not end_d:
            raw_end = item.get("EndDate") or item.get("DenNgay")
            end_d = parse_date_safely(raw_end)

        if not begin_d:
            continue
        if not end_d:
            end_d = begin_d + timedelta(weeks=15)

        # Generate each session occurrence
        curr_d = begin_d
        days_ahead = (target_weekday - curr_d.weekday()) % 7
        curr_d += timedelta(days=days_ahead)

        while curr_d <= end_d:
            start_dt = datetime.combine(curr_d, start_t)
            end_dt = datetime.combine(curr_d, end_t)

            title_prefix = f"[{subject_code}] " if subject_code else ""
            title = f"{title_prefix}{subject_name}"

            desc = (
                f"Môn học: {subject_name}\n"
                f"Mã LHP: {subject_code}\n"
                f"Giảng viên: {lecturer}\n"
                f"Tiết học: Tiết {start_p} - {end_p} ({start_t.strftime('%H:%M')} - {end_t.strftime('%H:%M')})\n"
                f"Phòng: {room}\n"
                f"Cơ sở: {campus}\n"
                f"Hệ thống: online.hcmute.edu.vn"
            )

            uid = f"hcmute-class-{subject_code or idx}-{curr_d.strftime('%Y%m%d')}-p{start_p}"
            loc = f"Phòng {room}, Trường ĐH SPKT TP.HCM ({campus})"

            events.append(
                CalendarEvent(
                    uid=uid,
                    title=title,
                    start_datetime=start_dt,
                    end_datetime=end_dt,
                    location=loc,
                    description=desc,
                    event_type="CLASS",
                    category="ACADEMIC",
                )
            )

            curr_d += timedelta(days=7)

    return events


def parse_exam_schedule(exam_data: Any) -> List[CalendarEvent]:
    """
    Parse exam schedule into calendar events.
    Supports both student/exam and student/showexambytime formats.
    """
    items = []
    if isinstance(exam_data, list):
        items = exam_data
    elif isinstance(exam_data, dict):
        if "result" in exam_data and isinstance(exam_data["result"], list):
            items = exam_data["result"]
        elif "data" in exam_data and isinstance(exam_data["data"], list):
            items = exam_data["data"]
        else:
            for v in exam_data.values():
                if isinstance(v, list):
                    items = v
                    break

    events: List[CalendarEvent] = []
    seen_exams = set()

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        subject = (
            item.get("CurriculumName")
            or item.get("TenHP")
            or item.get("TenMonHoc")
            or item.get("ClassCourseName")
            or "Thi môn học"
        ).strip()

        code = item.get("CurriculumID") or item.get("MaHP") or item.get("MaLHP") or ""
        room = item.get("PhongThi") or item.get("Phong") or "Xem thông báo"
        location = item.get("DiaDiem") or "HCMUTE"
        exam_type = item.get("KyThi") or "Thi kết thúc học phần"

        exam_date_raw = str(item.get("NgayThi") or item.get("DateExam") or "").strip()
        exam_time_raw = str(item.get("GioThi") or item.get("TimeExam") or "07:30").strip()
        duration = item.get("ThoiLuong") or item.get("ThoiGianLamBai") or 90

        if not exam_date_raw or exam_date_raw.lower() in ("null", "none"):
            continue

        # Parse date DD/MM/YYYY
        m_date = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", exam_date_raw)
        if not m_date:
            exam_d = parse_date_safely(exam_date_raw)
        else:
            d, m, y = map(int, m_date.groups())
            try:
                exam_d = date(y, m, d)
            except ValueError:
                exam_d = None

        if not exam_d:
            continue

        # Parse time: e.g. 13g00, 09g45, 13:00, 7h30
        hh, mm = 7, 30
        m_time = re.search(r"(\d{1,2})(?:g|h|:)(\d{2})", exam_time_raw, re.IGNORECASE)
        if m_time:
            hh, mm = int(m_time.group(1)), int(m_time.group(2))
        exam_t = time(hh, mm)

        start_dt = datetime.combine(exam_d, exam_t)
        try:
            dur_mins = int(duration)
        except (ValueError, TypeError):
            dur_mins = 90
        end_dt = start_dt + timedelta(minutes=dur_mins)

        # Deduplication
        dedup_key = f"{code}_{exam_d}_{exam_t}"
        if dedup_key in seen_exams:
            continue
        seen_exams.add(dedup_key)

        title = f"[LỊCH THI] {subject}"
        desc = (
            f"🎯 KỲ THI: {exam_type}\n"
            f"📚 Môn thi: {subject}\n"
            f"Mã học phần: {code}\n"
            f"🏢 Phòng thi: {room}\n"
            f"📍 Địa điểm: {location}\n"
            f"⏰ Giờ thi: {start_dt.strftime('%H:%M')} (Ngày {start_dt.strftime('%d/%m/%Y')})\n"
            f"⏳ Thời gian làm bài: {dur_mins} phút\n"
            f"Số báo danh (SBD): {item.get('SBD') or 'Theo danh sách'}\n"
            f"Ghi chú: {item.get('Note', '')}\n"
            f"Hệ thống: online.hcmute.edu.vn"
        )
        uid = f"hcmute-exam-{code or idx}-{exam_d.strftime('%Y%m%d')}-{hh}{mm}"

        events.append(
            CalendarEvent(
                uid=uid,
                title=title,
                start_datetime=start_dt,
                end_datetime=end_dt,
                location=f"Phòng {room}, {location}",
                description=desc,
                event_type="EXAM",
                category="EXAM",
            )
        )

    return events
