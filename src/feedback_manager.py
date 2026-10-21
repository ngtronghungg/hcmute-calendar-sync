"""
Feedback & Suggestion Management Module with 10-Level AI Evaluation
Categorizes student suggestions from 'Level 1: Cần thiết' to 'Level 10: Spam'.
Supports Google Gemini AI with intelligent heuristic fallback.
"""

import os
import json
import uuid
import re
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from src.telegram_bot import load_config

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FEEDBACKS_FILE = os.path.join(PROJECT_ROOT, "data", "feedbacks.json")

LEVEL_LABELS = {
    1: "Cấp 1: Cực kỳ khẩn cấp (Sập/Bảo mật/Sai TKB)",
    2: "Cấp 2: Rất cần thiết (Lỗi đồng bộ/Thiếu môn)",
    3: "Cấp 3: Tính năng giá trị cao (Đề xuất hữu ích)",
    4: "Cấp 4: Cải tiến trải nghiệm quan trọng (UI/UX)",
    5: "Cấp 5: Góp ý xây dựng (Phản hồi tích cực)",
    6: "Cấp 6: Tinh chỉnh nhỏ (Sở thích/Ưu tiên thấp)",
    7: "Cấp 7: Thắc mắc / Đã có tính năng",
    8: "Cấp 8: Chưa rõ ràng (Mơ hồ/Thiếu chi tiết)",
    9: "Cấp 9: Không khả thi (Ngoài phạm vi can thiệp)",
    10: "Cấp 10: Rác / Spam (Ký tự vô nghĩa/Tục tĩu)",
}

LEVEL_BADGES = {
    1: {"bg": "rgba(239, 68, 68, 0.25)", "color": "#f87171", "border": "rgba(239, 68, 68, 0.4)", "tag": "Khẩn cấp"},
    2: {"bg": "rgba(249, 115, 22, 0.22)", "color": "#fb923c", "border": "rgba(249, 115, 22, 0.4)", "tag": "Rất cần thiết"},
    3: {"bg": "rgba(245, 158, 11, 0.2)", "color": "#fbbf24", "border": "rgba(245, 158, 11, 0.35)", "tag": "Giá trị cao"},
    4: {"bg": "rgba(234, 179, 8, 0.18)", "color": "#facc15", "border": "rgba(234, 179, 8, 0.35)", "tag": "Cải tiến UX"},
    5: {"bg": "rgba(16, 185, 129, 0.18)", "color": "#34d399", "border": "rgba(16, 185, 129, 0.35)", "tag": "Xây dựng"},
    6: {"bg": "rgba(14, 165, 233, 0.18)", "color": "#38bdf8", "border": "rgba(14, 165, 233, 0.35)", "tag": "Tinh chỉnh"},
    7: {"bg": "rgba(168, 85, 247, 0.18)", "color": "#c084fc", "border": "rgba(168, 85, 247, 0.35)", "tag": "Thắc mắc"},
    8: {"bg": "rgba(148, 163, 184, 0.18)", "color": "#94a3b8", "border": "rgba(148, 163, 184, 0.35)", "tag": "Chưa rõ"},
    9: {"bg": "rgba(120, 113, 108, 0.18)", "color": "#a8a29e", "border": "rgba(120, 113, 108, 0.35)", "tag": "Ngoài phạm vi"},
    10: {"bg": "rgba(82, 82, 91, 0.25)", "color": "#71717a", "border": "rgba(82, 82, 91, 0.4)", "tag": "Spam / Rác"},
}


def load_feedbacks() -> List[Dict[str, Any]]:
    """Load all saved student feedbacks from disk."""
    if not os.path.exists(FEEDBACKS_FILE):
        return []
    try:
        with open(FEEDBACKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Feedback] Load error: {e}")
        return []


def save_feedbacks(feedbacks: List[Dict[str, Any]]) -> None:
    """Save all student feedbacks to disk."""
    try:
        os.makedirs(os.path.dirname(FEEDBACKS_FILE), exist_ok=True)
        with open(FEEDBACKS_FILE, "w", encoding="utf-8") as f:
            json.dump(feedbacks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Feedback] Save error: {e}")


def heuristic_evaluate(category: str, title: str, content: str) -> Dict[str, Any]:
    """
    Intelligent heuristic fallback evaluator.
    Accurately maps text into 1 of 10 levels if Gemini AI is unavailable.
    """
    full_text = f"{title} {content}".lower().strip()

    # Level 10: Spam / Garbage / Vulgarity / Keyboard smashing
    vulgar_pattern = r"\b(dm|dkm|đm|đkm|cl|cmn|vcl|lồn|buồi|địt|fuck|bitch|shit|chó|cút)\b"
    if re.search(vulgar_pattern, full_text):
        return {
            "level": 10,
            "level_label": LEVEL_LABELS[10],
            "ai_summary": "Nội dung chứa từ ngữ tiêu cực/tục tĩu, vi phạm chuẩn mực văn hóa ứng xử.",
            "sentiment": "Tiêu cực / Phá hoại",
            "feasibility": "Không khả thi",
            "ai_recommendation": "Xóa hoặc bỏ qua (Spam).",
        }

    if len(full_text) < 6 or re.search(r"([a-z0-9])\1{5,}", full_text) or re.search(r"^(asdf|test|abc|1234|qwer)", full_text):
        return {
            "level": 10,
            "level_label": LEVEL_LABELS[10],
            "ai_summary": "Nội dung quá ngắn, ký tự lặp ngẫu nhiên hoặc kiểm thử vô nghĩa.",
            "sentiment": "Rác / Thử nghiệm",
            "feasibility": "Không khả thi",
            "ai_recommendation": "Đánh dấu Spam cấp 10.",
        }

    # Level 9: Impossible / Out of scope
    if re.search(r"(sửa điểm|hack|gian lận|điểm danh hộ|đổi điểm|chấm lại|chửi giảng viên)", full_text):
        return {
            "level": 9,
            "level_label": LEVEL_LABELS[9],
            "ai_summary": "Yêu cầu can thiệp học vụ ngoài thẩm quyền kỹ thuật của phần mềm.",
            "sentiment": "Không phù hợp",
            "feasibility": "Không khả thi",
            "ai_recommendation": "Từ chối vì ngoài phạm vi hỗ trợ.",
        }

    # Level 1: Emergency / Severe
    if re.search(r"(sập|crash|lộ mật khẩu|bảo mật|security|lỗ hổng|không vào được|mất dữ liệu|toàn bộ sinh viên|toàn trường|sập server)", full_text):
        return {
            "level": 1,
            "level_label": LEVEL_LABELS[1],
            "ai_summary": "Báo cáo sự cố nghiêm trọng ảnh hưởng đến tính ổn định hoặc an toàn dữ liệu.",
            "sentiment": "Khẩn cấp",
            "feasibility": "Khả thi ngay",
            "ai_recommendation": "Quản trị viên cần kiểm tra và khắc phục khẩn cấp.",
        }

    # Level 2: High Priority Bug
    if category == "bug" or re.search(r"(lỗi|bug|sai phòng|sai giờ|trùng giờ|lệch tiết|không sync|lỗi ics|không hiện lịch thi|thiếu môn|sai ngày)", full_text):
        return {
            "level": 2,
            "level_label": LEVEL_LABELS[2],
            "ai_summary": "Phản ánh lỗi đồng bộ dữ liệu TKB hoặc Lịch thi ảnh hưởng tới việc học.",
            "sentiment": "Cần sửa chữa",
            "feasibility": "Khả thi ngay",
            "ai_recommendation": "Ưu tiên debug và sửa lỗi trong phiên bản tới.",
        }

    # Level 3: High-Value Feature
    if category == "feature" and re.search(r"(widget|đếm ngược|thông báo|zalo|học phí|nhắc nhở|apple watch|shortcuts|tự động|thời khóa biểu tuần)", full_text):
        return {
            "level": 3,
            "level_label": LEVEL_LABELS[3],
            "ai_summary": "Đề xuất tính năng mới có tính ứng dụng cao và mang lại giá trị cho số đông sinh viên.",
            "sentiment": "Tích cực",
            "feasibility": "Khả thi cao",
            "ai_recommendation": "Đưa vào danh mục tính năng ưu tiên phát triển (Feature Backlog).",
        }

    # Level 4: UI/UX Improvements
    if category == "ui" or re.search(r"(giao diện|font|màu|dark mode|nút|chữ nhỏ|dễ nhìn|chậm|tối ưu|lag|mượt|bố cục)", full_text):
        return {
            "level": 4,
            "level_label": LEVEL_LABELS[4],
            "ai_summary": "Đóng góp cải tiến trải nghiệm người dùng, tối ưu hiển thị trên thiết bị di động.",
            "sentiment": "Xây dựng",
            "feasibility": "Khả thi",
            "ai_recommendation": "Xem xét tinh chỉnh stylesheet và bố cục giao diện.",
        }

    # Level 7: FAQ / Already exists
    if re.search(r"(làm sao|cách nào|ở đâu|tại sao|có được không|hướng dẫn|cần hỗ trợ|chỉ em)", full_text):
        return {
            "level": 7,
            "level_label": LEVEL_LABELS[7],
            "ai_summary": "Thắc mắc về cách sử dụng phần mềm hoặc tính năng đã có sẵn trên giao diện.",
            "sentiment": "Cần hướng dẫn",
            "feasibility": "Đã có sẵn",
            "ai_recommendation": "Bổ sung câu hỏi thường gặp (FAQ) hoặc hướng dẫn sinh viên.",
        }

    # Level 8: Vague / Low details
    if len(full_text) < 18:
        return {
            "level": 8,
            "level_label": LEVEL_LABELS[8],
            "ai_summary": "Mô tả còn ngắn gọn, thiếu thông tin chi tiết hoặc ngữ cảnh cụ thể.",
            "sentiment": "Trung lập",
            "feasibility": "Cần làm rõ",
            "ai_recommendation": "Chờ thêm phản ánh từ sinh viên khác để xác thực.",
        }

    # Level 5: General Constructive
    if category == "feature" or re.search(r"(nên|mong muốn|hy vọng|đề xuất|ý kiến|cảm ơn|tuyệt vời)", full_text):
        return {
            "level": 5,
            "level_label": LEVEL_LABELS[5],
            "ai_summary": "Góp ý mang tính xây dựng, chia sẻ cảm nhận tích cực và gợi ý cải tiến.",
            "sentiment": "Tích cực",
            "feasibility": "Khả thi",
            "ai_recommendation": "Ghi nhận ý kiến đóng góp và gửi lời cảm ơn sinh viên.",
        }

    # Level 6: Minor preference
    return {
        "level": 6,
        "level_label": LEVEL_LABELS[6],
        "ai_summary": "Góp ý điều chỉnh nhỏ, phản ánh nhu cầu cá nhân hóa giao diện.",
        "sentiment": "Trung lập",
        "feasibility": "Khả thi trung bình",
        "ai_recommendation": "Lưu trữ để cân nhắc khi có bản cập nhật giao diện lớn.",
    }


def evaluate_with_gemini(category: str, title: str, content: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Call Google Gemini to analyze and evaluate feedback across 10 levels."""
    if not api_key:
        return None

    models = ["gemini-flash-latest", "gemini-1.5-flash", "gemini-2.0-flash"]
    headers = {"Content-Type": "application/json"}

    prompt_text = (
        "Bạn là Giám tuyển AI (AI Evaluator) chuyên đánh giá phản hồi, ý kiến đóng góp của sinh viên "
        "trường Đại học Sư phạm Kỹ thuật TP.HCM (HCMUTE) cho nền tảng HCMUTE Calendar & Sync.\n\n"
        "Nhiệm vụ: Phân tích nội dung góp ý bên dưới và phân loại CHÍNH XÁC vào đúng 1 trong 10 cấp độ từ Cần thiết -> Spam:\n"
        "1: Cấp 1: Cực kỳ khẩn cấp (Sập/Bảo mật/Sai TKB toàn trường)\n"
        "2: Cấp 2: Rất cần thiết (Lỗi đồng bộ/Thiếu môn học/Tính năng cốt lõi)\n"
        "3: Cấp 3: Tính năng giá trị cao (Đề xuất tính năng mới hữu ích cho số đông)\n"
        "4: Cấp 4: Cải tiến trải nghiệm quan trọng (UI/UX, hiệu năng di động)\n"
        "5: Cấp 5: Góp ý xây dựng (Phản hồi tốt, chia sẻ trải nghiệm)\n"
        "6: Cấp 6: Tinh chỉnh nhỏ (Sở thích cá nhân, màu sắc, ưu tiên thấp)\n"
        "7: Cấp 7: Thắc mắc / Đã có tính năng (Hỏi cách dùng hoặc hệ thống đã có)\n"
        "8: Cấp 8: Chưa rõ ràng (Mơ hồ, quá ngắn, thiếu chi tiết)\n"
        "9: Cấp 9: Không khả thi (Ngoài thẩm quyền phần mềm, yêu cầu vô lý)\n"
        "10: Cấp 10: Rác / Spam (Từ ngữ tục tĩu, gõ phím vô nghĩa, kiểm thử lăng nhăng)\n\n"
        f"THÔNG TIN GÓP Ý:\n"
        f"- Danh mục: {category}\n"
        f"- Tiêu đề: {title}\n"
        f"- Nội dung: {content}\n\n"
        "YÊU CẦU ĐẦU RA:\n"
        "Trả về DUY NHẤT một chuỗi JSON chuẩn (không dùng markdown code block, không giải thích ngoài JSON):\n"
        "{\n"
        '  "level": <số nguyên 1 đến 10>,\n'
        '  "level_label": "<Tên nhãn cấp độ tương ứng>",\n'
        '  "ai_summary": "<1-2 câu tóm tắt nội dung cốt lõi bằng tiếng Việt>",\n'
        '  "sentiment": "<Tích cực | Trung lập | Khẩn cấp | Thất vọng | Spam>",\n'
        '  "feasibility": "<Khả thi ngay | Cần nghiên cứu | Đã hỗ trợ | Không khả thi>",\n'
        '  "ai_recommendation": "<Lời khuyên ngắn gọn cho Quản trị viên>"\n'
        "}"
    )

    payload = {
        "contents": [
            {
                "parts": [{"text": prompt_text}]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 600,
        }
    }

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key.strip()}"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=3.5)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    out_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
                    # Strip any markdown fences if present
                    out_text = re.sub(r"^```json\s*", "", out_text)
                    out_text = re.sub(r"^```\s*", "", out_text)
                    out_text = re.sub(r"\s*```$", "", out_text).strip()
                    parsed = json.loads(out_text)
                    lvl = int(parsed.get("level", 5))
                    if 1 <= lvl <= 10:
                        parsed["level"] = lvl
                        parsed["level_label"] = LEVEL_LABELS.get(lvl, f"Cấp {lvl}")
                        return parsed
        except Exception as e:
            print(f"[Gemini Feedback Eval] Error ({model}): {e}")
            continue

    return None


def evaluate_feedback(category: str, title: str, content: str) -> Dict[str, Any]:
    """
    Unified evaluator: tries Gemini AI first, falls back to intelligent heuristics.
    """
    cfg = load_config()
    gemini_key = os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key", "").strip()

    if gemini_key:
        ai_res = evaluate_with_gemini(category, title, content, gemini_key)
        if ai_res:
            ai_res["evaluator"] = "Google Gemini AI"
            return ai_res

    # Heuristic fallback
    res = heuristic_evaluate(category, title, content)
    res["evaluator"] = "Heuristic AI Rules"
    return res


def submit_feedback(
    mssv: str,
    author_name: str,
    category: str,
    title: str,
    content: str,
    is_anonymous: bool = False,
) -> Dict[str, Any]:
    """Record new student feedback and immediately evaluate priority ranking."""
    title = (title or "").strip()
    content = (content or "").strip()
    if not title or not content:
        raise ValueError("Vui lòng nhập đầy đủ tiêu đề và nội dung góp ý.")

    feedbacks = load_feedbacks()
    fb_id = f"fb_{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:6]}"

    # Run AI evaluation
    eval_result = evaluate_feedback(category, title, content)

    feedback_record = {
        "id": fb_id,
        "mssv": str(mssv).strip(),
        "author_name": (author_name or "Sinh viên").strip(),
        "is_anonymous": bool(is_anonymous),
        "category": category or "feature",
        "title": title,
        "content": content,
        "ai_evaluation": eval_result,
        "status": "pending",  # pending | reviewed | resolved | dismissed
        "admin_note": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Store at top of the list
    feedbacks.insert(0, feedback_record)
    save_feedbacks(feedbacks)
    return feedback_record


def get_student_feedbacks(mssv: str) -> List[Dict[str, Any]]:
    """Return all feedbacks submitted by a given student MSSV."""
    mssv_str = str(mssv).strip()
    all_fb = load_feedbacks()
    return [fb for fb in all_fb if fb.get("mssv") == mssv_str]


def get_admin_feedbacks_overview(
    level_filter: Optional[int] = None,
    status_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get full feedbacks list and aggregated statistics for the Admin dashboard.
    """
    all_fb = load_feedbacks()

    # Calculate statistics
    total = len(all_fb)
    level_counts = {lvl: 0 for lvl in range(1, 11)}
    status_counts = {"pending": 0, "reviewed": 0, "resolved": 0, "dismissed": 0}

    for fb in all_fb:
        lvl = fb.get("ai_evaluation", {}).get("level", 5)
        if lvl in level_counts:
            level_counts[lvl] += 1
        st = fb.get("status", "pending")
        if st in status_counts:
            status_counts[st] += 1

    # Filtered list
    filtered = all_fb
    if level_filter is not None:
        filtered = [f for f in filtered if f.get("ai_evaluation", {}).get("level") == level_filter]
    if status_filter:
        filtered = [f for f in filtered if f.get("status") == status_filter]

    # Sort primarily by AI Level ascending (Level 1 Cần thiết nhất lên đầu), then by date descending
    filtered = sorted(
        filtered,
        key=lambda x: (
            x.get("ai_evaluation", {}).get("level", 5),
            -(datetime.fromisoformat(x.get("created_at", "2000-01-01T00:00:00+00:00")).timestamp())
        )
    )

    return {
        "total": total,
        "feedbacks": filtered,
        "stats": {
            "total": total,
            "urgent_count": level_counts[1] + level_counts[2],
            "feature_count": level_counts[3] + level_counts[4] + level_counts[5],
            "low_count": level_counts[6] + level_counts[7] + level_counts[8],
            "spam_count": level_counts[9] + level_counts[10],
            "level_counts": level_counts,
            "status_counts": status_counts,
        },
        "level_labels": LEVEL_LABELS,
        "level_badges": LEVEL_BADGES,
    }


def update_feedback_status(feedback_id: str, status: str, admin_note: str = "") -> bool:
    """Update review status and optional admin note."""
    all_fb = load_feedbacks()
    updated = False
    for fb in all_fb:
        if fb.get("id") == feedback_id:
            fb["status"] = status
            if admin_note is not None:
                fb["admin_note"] = admin_note
            fb["updated_at"] = datetime.now(timezone.utc).isoformat()
            updated = True
            break
    if updated:
        save_feedbacks(all_fb)
    return updated


def delete_feedback(feedback_id: str) -> bool:
    """Delete a feedback by ID."""
    all_fb = load_feedbacks()
    initial_len = len(all_fb)
    all_fb = [fb for fb in all_fb if fb.get("id") != feedback_id]
    if len(all_fb) != initial_len:
        save_feedbacks(all_fb)
        return True
    return False


def purge_spam_feedbacks() -> int:
    """Purge all feedbacks evaluated at Level 10 (Spam). Returns count removed."""
    all_fb = load_feedbacks()
    initial_len = len(all_fb)
    kept = [fb for fb in all_fb if fb.get("ai_evaluation", {}).get("level") != 10]
    removed = initial_len - len(kept)
    if removed > 0:
        save_feedbacks(kept)
    return removed


def re_evaluate_feedback(feedback_id: str) -> Optional[Dict[str, Any]]:
    """Re-run AI evaluation on a specific feedback."""
    all_fb = load_feedbacks()
    target_fb = None
    for fb in all_fb:
        if fb.get("id") == feedback_id:
            target_fb = fb
            break
    if not target_fb:
        return None

    new_eval = evaluate_feedback(
        target_fb.get("category", "feature"),
        target_fb.get("title", ""),
        target_fb.get("content", ""),
    )
    target_fb["ai_evaluation"] = new_eval
    target_fb["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_feedbacks(all_fb)
    return target_fb
