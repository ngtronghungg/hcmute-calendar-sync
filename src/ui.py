"""
Rich Console UI and Formatting for HCMUTE Calendar Tool
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Confirm, Prompt

from src.schedule_parser import CalendarEvent
from src.notice_analyzer import ConsiderationItem

console = Console()


def print_banner():
    banner_text = Text()
    banner_text.append("🎓 HCMUTE CALENDAR & REMINDER SYNC TOOL\n", style="bold cyan")
    banner_text.append("Đồng bộ Thời khóa biểu & Nhắc nhở thông báo từ online.hcmute.edu.vn", style="italic white")
    console.print(Panel(banner_text, border_style="blue", expand=False))


def print_user_profile(profile: Dict[str, Any]):
    table = Table(title="👤 Thông tin Sinh viên HCMUTE", show_header=False, border_style="green")
    table.add_column("Key", style="bold yellow")
    table.add_column("Value", style="bold white")

    table.add_row("Mã số sinh viên (MSSV)", str(profile.get("Id", "N/A")))
    table.add_row("Họ và tên", str(profile.get("FullName", profile.get("Username", "N/A"))))
    table.add_row("Vai trò / Hệ đào tạo", str(profile.get("Role", "N/A")))
    table.add_row("Trạng thái học", str(profile.get("StudyStatusName", "Đang học")))
    console.print(table)


def print_schedule_table(events: List[CalendarEvent], max_display: int = 15):
    """Print class schedule in a table."""
    table = Table(title=f"📅 Danh sách Thời Khóa Biểu (Tổng: {len(events)} buổi)", border_style="cyan")
    table.add_column("Thời gian", style="bold yellow", width=22)
    table.add_column("Môn học & Mã LHP", style="bold cyan")
    table.add_column("Phòng", style="bold magenta", width=12)
    table.add_column("Địa điểm", style="white")

    # Sort events by start date
    sorted_events = sorted(events, key=lambda x: x.start_datetime)
    for ev in sorted_events[:max_display]:
        time_str = ev.start_datetime.strftime("%d/%m/%Y (%a) %H:%M")
        table.add_row(time_str, ev.title, ev.location.split(",")[0], ev.location)

    if len(events) > max_display:
        table.add_row("...", f"... và {len(events) - max_display} buổi học khác ...", "...", "...")

    console.print(table)


def print_consideration_table(items: List[ConsiderationItem]):
    """Print considered announcements in a structured review table."""
    table = Table(
        title="📢 ĐÁNH GIÁ THÔNG BÁO & ĐỀ XUẤT NHẮC NHỞ (CONSIDERATION)",
        border_style="yellow",
        show_lines=True,
    )
    table.add_column("STT", style="dim", width=4)
    table.add_column("Ưu tiên", width=10)
    table.add_column("Danh mục", style="bold cyan", width=16)
    table.add_column("Hạn chót (Deadline)", style="bold red", width=18)
    table.add_column("Việc cần làm & Lý do", style="white")

    for idx, item in enumerate(items, 1):
        # Priority badge
        if item.priority == "HIGH":
            p_badge = Text("🔥 CAO", style="bold red")
        elif item.priority == "MEDIUM":
            p_badge = Text("⚡ VỪA", style="bold yellow")
        else:
            p_badge = Text("ℹ️ THẤP", style="dim white")

        deadline_str = item.deadline.strftime("%d/%m/%Y %H:%M") if item.deadline else "Cần xem kỹ"

        desc = Text()
        desc.append(f"{item.title}\n", style="bold white")
        desc.append(f"👉 Cần làm: {item.action_required}\n", style="italic green")
        desc.append(f"💡 Đề xuất: {item.recommendation}", style="dim")

        table.add_row(str(idx), p_badge, item.category, deadline_str, desc)

    console.print(table)


def confirm_add_reminders(items: List[ConsiderationItem]) -> List[ConsiderationItem]:
    """
    Prompt user to select which consideration items to add to Google Calendar.
    """
    if not items:
        return []

    print_consideration_table(items)
    console.print("\n[bold yellow]👉 Hệ thống phát hiện các hạn chót quan trọng trên.[/bold yellow]")

    choice = Prompt.ask(
        "Bạn muốn xử lý các nhắc nhở này thế nào?",
        choices=["all", "select", "none"],
        default="all",
    )

    if choice == "all":
        return items
    elif choice == "none":
        return []
    else:
        selected = []
        for idx, item in enumerate(items, 1):
            if Confirm.ask(f"Thêm nhắc nhở #{idx}: '{item.title}'?", default=True):
                selected.append(item)
        return selected
