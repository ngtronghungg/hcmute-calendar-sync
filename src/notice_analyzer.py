"""
HCMUTE Announcement & Consideration Analyzer
Analyzes announcements, messages, and fee debt status to identify critical deadlines
and recommend calendar reminders (tuition, civic education, certifications, exams, etc.).
"""

import re
import base64
from datetime import datetime, date, time, timedelta
from typing import List, Dict, Any, Optional
from dateutil import parser as dt_parser

from src.schedule_parser import CalendarEvent

HCMUTE_XOR_KEY = b"PSC@HcmuteMsgKey2024"


def decrypt_hcmute_body(raw_body: str) -> str:
    """Decrypt HCMUTE XOR-encrypted and base64-encoded message body."""
    if not raw_body:
        return ""
    try:
        data = base64.b64decode(raw_body)
        decrypted = bytes([b ^ HCMUTE_XOR_KEY[i % len(HCMUTE_XOR_KEY)] for i, b in enumerate(data)])
        return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        return raw_body


class ConsiderationItem:
    """Represents an analyzed announcement item evaluated for reminder creation."""

    def __init__(
        self,
        item_id: str,
        title: str,
        category: str,
        priority: str,  # HIGH, MEDIUM, LOW
        deadline: Optional[datetime],
        summary: str,
        action_required: str,
        recommendation: str,
        source_title: str,
        source_id: str,
        source_date: Optional[datetime] = None,
        confidence_score: float = 0.8,
        is_reminder_needed: bool = True,
        notice_type: str = "general",
        full_content: str = "",
    ):
        self.item_id = str(item_id)
        self.title = title
        self.category = category
        self.priority = priority
        self.deadline = deadline
        self.summary = summary
        self.action_required = action_required
        self.recommendation = recommendation
        self.source_title = source_title
        self.source_id = str(source_id)
        self.source_date = source_date
        self.confidence_score = confidence_score
        self.is_reminder_needed = is_reminder_needed
        self.notice_type = notice_type
        self.full_content = full_content

    def to_calendar_event(self) -> Optional[CalendarEvent]:
        """Convert this reminder consideration into a CalendarEvent."""
        if not self.deadline:
            return None

        start_dt = self.deadline - timedelta(hours=1)
        end_dt = self.deadline

        desc = (
            f"🔔 [NHẮC NHỞ HCMUTE] {self.title}\n\n"
            f"⚠️ Mức độ ưu tiên: {self.priority}\n"
            f"📋 Phân loại: {self.category}\n"
            f"⏰ Hạn chót: {self.deadline.strftime('%d/%m/%Y %H:%M')}\n"
            f"📝 Việc cần làm: {self.action_required}\n"
            f"💡 Lý do: {self.recommendation}\n\n"
            f"📌 Nguồn thông báo: {self.source_title}\n"
            f"Hệ thống: online.hcmute.edu.vn"
        )

        return CalendarEvent(
            uid=f"hcmute-reminder-{self.item_id}",
            title=f"⚠️ [HẠN CHÓT] {self.title}",
            start_datetime=start_dt,
            end_datetime=end_dt,
            location="HCMUTE Online Portal (https://online.hcmute.edu.vn)",
            description=desc,
            event_type="DEADLINE",
            category=self.category,
        )

    def __repr__(self) -> str:
        d_str = self.deadline.strftime("%d/%m/%Y %H:%M") if self.deadline else "Không rõ ngày"
        return f"<Consideration [{self.priority}] {self.title} (Hạn: {d_str})>"


def extract_dates_from_text(text: str) -> List[datetime]:
    """
    Accurately extract action deadline dates from Vietnamese announcement text.
    Filters out past dates and formal document citation dates (Quyết định ngày...).
    """
    found_dts: List[datetime] = []
    if not text:
        return found_dts

    # Remove HTML tags
    clean_text = re.sub(r"<[^>]+>", " ", text)
    clean_text = re.sub(r"\s+", " ", clean_text)

    # Strip formal citation dates like: "Quyết định số 123/QĐ ngày 15/08/2020", "Luật số ... ngày ..."
    stripped_text = re.sub(
        r"(?:Quyết\s*định|QĐ|Nghị\s*định|Thông\s*tư|Công\s*văn|Luật|Số:?)\s*(?:số)?\s*[\w\d/._-]+\s*ngày\s*\d{1,2}[/-]\d{1,2}[/-]\d{4}",
        "",
        clean_text,
        flags=re.IGNORECASE,
    )

    now = datetime.now()

    # 1. Pattern with explicit deadline time and date: e.g. "9g00 ngày 29/9/2026 đến hết ngày 31/10/2026"
    p_timed_deadline = re.finditer(
        r"(?:đến\s*(?:hết\s*)?ngày|trước\s*ngày|hạn\s*chót\s*:?|hạn\s*cuối\s*:?)\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s*(?:vào\s*lúc|lúc)\s*(\d{1,2})(?:g|h|:)(\d{2}))?",
        stripped_text,
        re.IGNORECASE,
    )
    for m in p_timed_deadline:
        day, month, year, hh, mm = m.groups()
        day, month, year = int(day), int(month), int(year)
        hour = int(hh) if hh else 23
        minute = int(mm) if mm else 59
        try:
            dt = datetime(year, month, day, hour, minute)
            if dt >= now - timedelta(days=1):
                found_dts.append(dt)
        except ValueError:
            pass

    # 2. Pattern with range: "từ ngày ... đến (hết) ngày DD/MM/YYYY"
    p_range = re.finditer(
        r"(?:từ\s*ngày\s*\d{1,2}[/-]\d{1,2}[/-]\d{4})?\s*(?:đến|~|-)\s*(?:hết\s*)?(?:ngày\s*)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
        stripped_text,
        re.IGNORECASE,
    )
    for m in p_range:
        day, month, year = map(int, m.groups()[-3:])
        try:
            dt = datetime(year, month, day, 23, 59)
            if dt >= now - timedelta(days=1):
                found_dts.append(dt)
        except ValueError:
            pass

    # 3. Pattern with "thời gian thực hiện ... đến ngày DD/MM/YYYY"
    p_action = re.finditer(
        r"thời\s*gian\s*(?:thực\s*hiện|nộp|đóng|làm\s*bài|đăng\s*ký)[^.!?]{0,80}?(?:đến|hết)\s*(?:ngày\s*)?(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
        stripped_text,
        re.IGNORECASE,
    )
    for m in p_action:
        day, month, year = map(int, m.groups()[-3:])
        try:
            dt = datetime(year, month, day, 23, 59)
            if dt >= now - timedelta(days=1):
                found_dts.append(dt)
        except ValueError:
            pass

    return found_dts


class NoticeAnalyzer:
    """Analyzes HCMUTE portal announcements and formulates recommendations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        rules_cfg = self.config.get("consideration", {}).get("rules", {})
        self.rules = rules_cfg or self._default_rules()

    def _default_rules(self) -> Dict[str, Any]:
        return {
            "TUITION": {
                "title": "Hạn nộp học phí & Công nợ",
                "keywords": ["học phí", "hoc phi", "đóng tiền", "gia hạn", "công nợ", "nợ học phí"],
                "priority": "HIGH",
                "recommendation": "Hạn chót học phí rất quan trọng để không bị hủy thời khóa biểu hoặc cấm thi.",
                "action": "Đóng học phí qua cổng thanh toán VNPAY / Ngân hàng theo hướng dẫn.",
            },
            "CIVIC_EDUCATION": {
                "title": "Sinh hoạt công dân",
                "keywords": ["sinh hoạt công dân", "shcd", "đầu khóa", "giữa khóa", "cuối khóa", "bài thu hoạch"],
                "priority": "HIGH",
                "recommendation": "Sinh hoạt công dân là điều kiện bắt buộc để xét điểm rèn luyện và tốt nghiệp.",
                "action": "Tham gia lớp học và hoàn thành bài kiểm tra thu hoạch trực tuyến trên hệ thống.",
            },
            "CERTIFICATE": {
                "title": "Nộp chứng chỉ ngoại ngữ / tin học",
                "keywords": ["chứng chỉ", "tiếng anh", "tin học", "chuẩn đầu ra", "toeic", "ielts", "ic3", "mos", "xét tốt nghiệp"],
                "priority": "HIGH",
                "recommendation": "Cần nộp chứng chỉ đúng đợt xét chuẩn đầu ra hoặc miễn giảm môn học.",
                "action": "Nộp hồ sơ minh chứng chứng chỉ online hoặc tại văn phòng khoa/phòng đào tạo.",
            },
            "REGISTRATION": {
                "title": "Đăng ký môn học / Rút môn",
                "keywords": ["đăng ký môn học", "dkmh", "rút môn học", "hủy học phần", "đăng ký học lại"],
                "priority": "HIGH",
                "recommendation": "Đăng ký hoặc hủy môn đúng hạn để hoàn thiện kế hoạch học tập.",
                "action": "Truy cập cổng đăng ký môn học và thực hiện thao tác đúng thời gian quy định.",
            },
            "SURVEY": {
                "title": "Khảo sát ý kiến sinh viên",
                "keywords": ["khảo sát", "khao sat", "đánh giá giảng viên", "đánh giá môn học", "lấy ý kiến"],
                "priority": "MEDIUM",
                "recommendation": "Khảo sát sinh viên là điều kiện để xem điểm thi hoặc đăng ký môn học đợt sau.",
                "action": "Truy cập mục Đánh giá giảng viên / Khảo sát trên online.hcmute.edu.vn và gửi phiếu.",
            },
            "EXAM": {
                "title": "Lịch thi & Phúc khảo",
                "keywords": ["lịch thi", "phúc khảo", "hoãn thi", "thi bù", "điểm thi"],
                "priority": "HIGH",
                "recommendation": "Lịch thi hoặc thời hạn nộp đơn phúc khảo kết thúc học phần.",
                "action": "Kiểm tra phòng thi, giờ thi hoặc nộp đơn phúc khảo trực tuyến nếu cần.",
            },
        }

    def analyze_announcement(
        self,
        notice: Dict[str, Any],
        notice_type: str = "general",
    ) -> Optional[ConsiderationItem]:
        """
        Analyze an announcement dictionary (general or personal).
        Decrypts body if needed and extracts future deadlines accurately.
        """
        title = (
            notice.get("MessageSubject")
            or notice.get("TieuDe")
            or notice.get("Title")
            or notice.get("Subject")
            or ""
        ).strip()

        # Get body and decrypt if encrypted base64
        raw_content = (
            notice.get("MessageBody")
            or notice.get("NoiDung")
            or notice.get("Content")
            or ""
        )
        decrypted_content = decrypt_hcmute_body(raw_content) if notice_type == "personal" else raw_content

        if not title and decrypted_content:
            clean_snippet = re.sub(r"<[^>]+>", " ", decrypted_content)
            clean_snippet = " ".join(clean_snippet.split())
            m_subj = re.search(r"(?:thông\s*báo|kế\s*hoạch|về\s*việc)[^.!\n]{5,100}", clean_snippet, re.IGNORECASE)
            if m_subj:
                title = m_subj.group(0).strip().capitalize()
            elif clean_snippet:
                title = clean_snippet[:80].strip() + ("..." if len(clean_snippet) > 80 else "")
            else:
                title = "Thông báo học vụ"

        notice_id = str(
            notice.get("MessageID")
            or notice.get("MaTin")
            or notice.get("Id")
            or hash(title)
        )

        raw_created = (
            notice.get("CreationDate")
            or notice.get("CreateDate")
            or notice.get("NgayTao")
        )
        created_dt: Optional[datetime] = None
        if raw_created:
            try:
                created_dt = dt_parser.parse(str(raw_created), dayfirst=True)
            except Exception:
                pass

        full_text = f"{title}\n{decrypted_content}"
        lower_text = full_text.lower()

        # Match category rules
        matched_category = None
        matched_rule = None
        for cat_key, rule in self.rules.items():
            keywords = rule.get("keywords", [])
            for kw in keywords:
                if kw in lower_text:
                    matched_category = cat_key
                    matched_rule = rule
                    break
            if matched_category:
                break

        if not matched_category or not matched_rule:
            if any(term in lower_text for term in ["hạn chót", "trước ngày", "thời hạn", "hạn nộp", "đến hết ngày"]):
                matched_category = "GENERAL"
                matched_rule = {
                    "title": "Thông báo có hạn chót",
                    "priority": "MEDIUM",
                    "recommendation": "Thông báo có thời hạn cần thực hiện.",
                    "action": "Xem toàn văn thông báo để làm theo hướng dẫn.",
                }
            else:
                return None

        # Extract dates
        dates = extract_dates_from_text(full_text)
        now = datetime.now()

        # Filter strictly future dates
        future_dates = [d for d in dates if d >= now - timedelta(days=1)]
        if not future_dates:
            # If no active future deadline was found, do NOT show it as an upcoming reminder!
            return None

        # Choose the earliest future deadline
        deadline_dt = min(future_dates)

        clean_title = f"{matched_rule.get('title')}: {title}"
        if len(clean_title) > 95:
            clean_title = clean_title[:92] + "..."

        item = ConsiderationItem(
            item_id=notice_id,
            title=clean_title,
            category=matched_category,
            priority=matched_rule.get("priority", "MEDIUM"),
            deadline=deadline_dt,
            summary=title,
            action_required=matched_rule.get("action", "Xem nội dung thông báo"),
            recommendation=matched_rule.get("recommendation", "Cần lưu ý thời hạn thông báo"),
            source_title=title,
            source_id=notice_id,
            source_date=created_dt,
            confidence_score=0.95,
            is_reminder_needed=True,
            notice_type=notice_type,
            full_content=decrypted_content,
        )
        return item

    def analyze_all_announcements(
        self,
        notices: List[Dict[str, Any]],
        notice_type: str = "general",
        lookback_days: int = 45,
    ) -> List[ConsiderationItem]:
        """
        Analyze a list of notices and filter down to items requiring action with future deadlines.
        """
        results: List[ConsiderationItem] = []
        seen_ids = set()
        cutoff_date = datetime.now() - timedelta(days=lookback_days)

        for notice in notices:
            item = self.analyze_announcement(notice, notice_type=notice_type)
            if not item:
                continue

            if item.item_id in seen_ids:
                continue
            seen_ids.add(item.item_id)

            # Skip notices with creation date too old unless deadline is very soon
            if item.source_date and item.source_date < cutoff_date:
                if not item.deadline or (item.deadline - datetime.now()).days > 30:
                    continue

            results.append(item)

        # Sort: STRICT ASCENDING BY DEADLINE (nearest upcoming deadline comes first!)
        results.sort(key=lambda x: x.deadline or datetime(2099, 1, 1))
        return results

    def analyze_tuition_debt(self, fee_data: Any) -> Optional[ConsiderationItem]:
        """
        Analyze tuition balance from GetStudentFeedebt.
        If there is an outstanding balance, create an immediate high-priority consideration.
        """
        if not fee_data:
            return None

        # Inspect typical fields: ConNo, HocPhi, PhaiThu, DaThu, SoTien
        con_no = 0.0
        details = ""

        if isinstance(fee_data, list):
            items = fee_data
        elif isinstance(fee_data, dict):
            items = [fee_data]
            if "result" in fee_data and isinstance(fee_data["result"], list):
                items = fee_data["result"]
        else:
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            no = (
                item.get("ConNo")
                or item.get("SoTienConLai")
                or item.get("DebtAmount")
                or item.get("NoCuoiKy")
                or 0
            )
            try:
                con_no += float(no)
            except (ValueError, TypeError):
                pass

            hoc_phi = item.get("HocPhi") or item.get("SoTien") or 0
            hk = item.get("HocKy") or item.get("TermName") or ""
            if hoc_phi:
                details += f"Học kỳ {hk}: Còn nợ {con_no:,.0f} VNĐ. "

        if con_no > 0:
            return ConsiderationItem(
                item_id="fee-debt-status",
                title=f"⚠️ Cảnh báo nợ học phí ({con_no:,.0f} VNĐ)",
                category="TUITION",
                priority="HIGH",
                deadline=datetime.now() + timedelta(days=7),  # Urge within a week
                summary=f"Hệ thống ghi nhận bạn còn nợ học phí {con_no:,.0f} VNĐ.",
                action_required="Đăng nhập online.hcmute.edu.vn mục Học phí để thanh toán ngay.",
                recommendation="Nợ học phí sẽ dẫn đến việc bị cấm thi hoặc không được đăng ký môn học.",
                source_title="Trạng thái học phí cá nhân (GetStudentFeedebt)",
                source_id="fee_debt",
                source_date=datetime.now(),
                confidence_score=1.0,
                is_reminder_needed=True,
            )
        return None
