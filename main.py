"""
HCMUTE Calendar & Reminder Sync Tool
CLI application for extracting schedule & analyzing announcements from online.hcmute.edu.vn
and syncing to Google Calendar.
"""

import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import os

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import getpass
import typer
from typing import Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel

from src.hcmute_api import HCMUTEClient
from src.schedule_parser import parse_semester_schedule, parse_exam_schedule, CalendarEvent
from src.notice_analyzer import NoticeAnalyzer, ConsiderationItem
from src.google_calendar import GoogleCalendarSync
from src.ics_exporter import export_events_to_ics
from src.ui import (
    console,
    print_banner,
    print_user_profile,
    print_schedule_table,
    print_consideration_table,
    confirm_add_reminders,
)

app = typer.Typer(help="HCMUTE Schedule & Reminder Google Calendar Sync Tool")

CONFIG_PATH = "config/config.json"


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


def ensure_authenticated(client: HCMUTEClient) -> bool:
    """Ensure client is logged in; if not, prompt user."""
    if client.is_authenticated():
        return True

    console.print("[yellow]Chưa tìm thấy phiên đăng nhập còn hạn. Vui lòng đăng nhập tài khoản HCMUTE:[/yellow]")
    username = typer.prompt("Mã số sinh viên (MSSV)")
    password = typer.prompt("Mật khẩu", hide_input=True)

    with console.status("[bold green]Đang xác thực với online.hcmute.edu.vn..."):
        try:
            profile = client.login(username, password)
            console.print("[bold green]✔ Đăng nhập thành công![/bold green]")
            print_user_profile(profile)
            return True
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi đăng nhập:[/bold red] {e}")
            return False


@app.command()
def login():
    """Đăng nhập tài khoản sinh viên HCMUTE."""
    print_banner()
    client = get_client()
    client.logout()

    username = typer.prompt("Mã số sinh viên (MSSV)")
    password = typer.prompt("Mật khẩu", hide_input=True)

    with console.status("[bold green]Đang kết nối tới máy chủ HCMUTE..."):
        try:
            profile = client.login(username, password)
            console.print("[bold green]✔ Đăng nhập thành công! Đã lưu phiên làm việc an toàn.[/bold green]")
            print_user_profile(profile)
        except Exception as e:
            console.print(f"[bold red]❌ Đăng nhập thất bại:[/bold red] {e}")


@app.command()
def logout():
    """Đăng xuất và xóa phiên lưu trữ."""
    client = get_client()
    client.logout()
    console.print("[green]✔ Đã đăng xuất và xóa token phiên làm việc.[/green]")


@app.command()
def status():
    """Kiểm tra trạng thái đăng nhập và tài khoản hiện tại."""
    print_banner()
    client = get_client()
    if client.is_authenticated():
        console.print("[bold green]✔ Trạng thái: Đã đăng nhập.[/bold green]")
        profile = client.get_user_profile()
        if profile:
            print_user_profile(profile)
    else:
        console.print("[yellow]Chưa đăng nhập. Hãy chạy lệnh 'python main.py login' để đăng nhập.[/yellow]")


@app.command()
def notices(
    auto_add: bool = typer.Option(False, "--auto-add", "-y", help="Tự động đồng bộ các nhắc nhở đã phát hiện"),
    lookback_days: int = typer.Option(90, "--days", "-d", help="Số ngày thông báo cần quét"),
):
    """Đọc thông báo từ portal HCMUTE, phân tích hạn chót và đưa ra consideration (nhắc nhở)."""
    print_banner()
    client = get_client()
    if not ensure_authenticated(client):
        return

    analyzer = NoticeAnalyzer(load_config())
    raw_notices = []
    fee_debt_item = None

    with console.status("[bold cyan]Đang tải thông báo chung và thông báo cá nhân..."):
        try:
            general_notices = client.get_general_announcements()
            raw_notices.extend(general_notices)
        except Exception as e:
            console.print(f"[dim red]Không lấy được thông báo chung: {e}[/dim red]")

        try:
            personal_notices = client.get_personal_messages()
            raw_notices.extend(personal_notices)
        except Exception as e:
            console.print(f"[dim red]Không lấy được thông báo cá nhân: {e}[/dim red]")

        try:
            fee_data = client.get_fee_debt()
            fee_debt_item = analyzer.analyze_tuition_debt(fee_data)
        except Exception:
            pass

    console.print(f"[green]✔ Đã tải {len(raw_notices)} thông báo từ hệ thống.[/green]")

    with console.status("[bold yellow]Đang phân tích nội dung, trích xuất hạn chót & đánh giá..."):
        considered_items = analyzer.analyze_all_announcements(raw_notices, lookback_days=lookback_days)
        if fee_debt_item:
            considered_items.insert(0, fee_debt_item)

    if not considered_items:
        console.print("[bold green]Không phát hiện thông báo nào có hạn chót gấp cần thêm vào Reminder.[/bold green]")
        return

    # Review items
    if auto_add:
        selected_items = considered_items
        print_consideration_table(considered_items)
    else:
        selected_items = confirm_add_reminders(considered_items)

    if not selected_items:
        console.print("[yellow]Không có nhắc nhở nào được chọn để thêm vào lịch.[/yellow]")
        return

    # Convert to Calendar Events
    reminder_events = []
    for item in selected_items:
        ev = item.to_calendar_event()
        if ev:
            reminder_events.append(ev)

    console.print(f"[green]✔ Đã chuẩn bị {len(reminder_events)} sự kiện nhắc nhở.[/green]")

    # Sync to Google Calendar or export
    sync_events_handler(reminder_events, calendar_type="REMINDER")


@app.command()
def sync(
    year: Optional[str] = typer.Option(None, "--year", "-y", help="Năm học (VD: 2026-2027)"),
    term: Optional[str] = typer.Option(None, "--term", "-t", help="Học kỳ (VD: 1, 2, 3)"),
    skip_notices: bool = typer.Option(False, "--skip-notices", help="Bỏ qua phần phân tích thông báo"),
    auto_remind: bool = typer.Option(False, "--auto-remind", help="Tự động đồng bộ nhắc nhở mà không cần hỏi lại"),
):
    """
    Đồng bộ toàn diện:
    1. Lấy Thời khóa biểu học kỳ từ online.hcmute.edu.vn
    2. Đọc thông báo, phân tích hạn chót (học phí, sinh hoạt công dân, khảo sát...)
    3. Thêm thẳng vào Google Calendar (hoặc xuất file .ics)
    """
    print_banner()
    client = get_client()
    if not ensure_authenticated(client):
        return

    all_events: list[CalendarEvent] = []

    # 1. Fetch Year & Term
    with console.status("[bold cyan]Đang kiểm tra năm học và học kỳ hiện tại..."):
        try:
            yt_data = client.get_year_and_term()
            curr_year = year or yt_data.get("CurrentYear") or yt_data.get("NamHienTai") or "2026-2027"
            curr_term = term or yt_data.get("CurrentTerm") or yt_data.get("HocKyHienTai") or "HK01"
        except Exception:
            curr_year = year or "2026-2027"
            curr_term = term or "HK01"

    console.print(f"[bold cyan]📅 Năm học:[/bold cyan] {curr_year} | [bold cyan]Học kỳ:[/bold cyan] {curr_term}")

    # 2. Fetch Class Schedule
    with console.status(f"[bold green]Đang lấy Thời Khóa Biểu học kỳ {curr_term} ({curr_year})..."):
        try:
            raw_sched = client.get_schedule_semester(curr_year, curr_term)
            class_events = parse_semester_schedule(raw_sched)
            all_events.extend(class_events)
            console.print(f"[bold green]✔ Đã lấy thành công {len(class_events)} buổi học.[/bold green]")
            if class_events:
                print_schedule_table(class_events)
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi khi lấy TKB:[/bold red] {e}")

    # 3. Fetch Exam Schedule (both by time and by term)
    with console.status("[bold magenta]Đang kiểm tra lịch thi..."):
        try:
            all_raw_exams = []
            try:
                all_raw_exams.extend(client.get_exams_by_time())
            except Exception:
                pass
            try:
                all_raw_exams.extend(client.get_exam_schedule(curr_year, curr_term))
            except Exception:
                pass

            exam_events = parse_exam_schedule(all_raw_exams)
            if exam_events:
                console.print(f"[bold magenta]✔ Đã phát hiện {len(exam_events)} môn thi![/bold magenta]")
                all_events.extend(exam_events)
        except Exception:
            pass

    # 4. Analyze Announcements & Consideration
    if not skip_notices:
        analyzer = NoticeAnalyzer(load_config())
        raw_notices = []
        with console.status("[bold yellow]Đang đọc thông báo portal & kiểm tra hạn đóng học phí, SHCD..."):
            try:
                raw_notices.extend(client.get_general_announcements())
            except Exception:
                pass
            try:
                raw_notices.extend(client.get_personal_messages())
            except Exception:
                pass

            considered = analyzer.analyze_all_announcements(raw_notices)
            try:
                fee_item = analyzer.analyze_tuition_debt(client.get_fee_debt())
                if fee_item:
                    considered.insert(0, fee_item)
            except Exception:
                pass

        if considered:
            if auto_remind:
                selected_reminders = considered
                print_consideration_table(considered)
            else:
                selected_reminders = confirm_add_reminders(considered)

            for item in selected_reminders:
                ev = item.to_calendar_event()
                if ev:
                    all_events.append(ev)

    if not all_events:
        console.print("[yellow]Không có sự kiện hoặc thời khóa biểu nào để đồng bộ.[/yellow]")
        return

    # 5. Sync to Google Calendar & Export .ics
    sync_events_handler(all_events, calendar_type="ALL")


def sync_events_handler(events: list[CalendarEvent], calendar_type: str = "ALL"):
    """Handle syncing events to Google Calendar and exporting .ics."""
    cfg = load_config()
    g_cfg = cfg.get("google_calendar", {})

    creds_path = g_cfg.get("credentials_path", "config/credentials.json")
    token_path = g_cfg.get("token_path", "data/google_token.json")

    # Always generate .ics file as an immediate fallback & backup
    ics_file = "data/hcmute_schedule.ics"
    export_events_to_ics(events, output_path=ics_file, calendar_name="HCMUTE Calendar & Reminders")
    console.print(f"[bold green]✔ Đã xuất file lịch (.ics):[/bold green] [cyan]{os.path.abspath(ics_file)}[/cyan]")
    console.print("[dim](Bạn có thể mở trực tiếp file .ics này để import vào Google Calendar, iPhone hoặc Outlook bất cứ lúc nào)[/dim]")

    # Check Google Calendar API sync
    gcal = GoogleCalendarSync(credentials_path=creds_path, token_path=token_path)
    if not gcal.is_configured():
        console.print(
            Panel(
                "[bold yellow]⚠️ Chưa cấu hình file Google OAuth Credentials (config/credentials.json).[/bold yellow]\n\n"
                "Để đồng bộ tự động trực tiếp vào Google Calendar:\n"
                "1. Truy cập https://console.cloud.google.com/ và tạo OAuth Client ID (Desktop App).\n"
                "2. Tải file JSON và đổi tên thành 'config/credentials.json'.\n"
                "3. Chạy lại lệnh này để hoàn tất kết nối tự động!\n\n"
                "👉 Hiện tại, bạn có thể dùng file [bold cyan]data/hcmute_schedule.ics[/bold cyan] vừa tạo ở trên để import thủ công 1-click vào Google Calendar ngay lập tức!",
                title="Thông báo Google Calendar",
                border_style="yellow",
            )
        )
        return

    # Run Google Calendar Sync
    console.print("\n[bold cyan]🔄 Đang kết nối và đồng bộ lên Google Calendar...[/bold cyan]")
    try:
        if not gcal.authenticate(interactive=True):
            console.print("[red]Không thể xác thực Google Calendar API.[/red]")
            return

        cal_name = g_cfg.get("schedule_calendar_name", "HCMUTE - Thời Khóa Biểu")
        if calendar_type == "REMINDER":
            cal_name = g_cfg.get("reminder_calendar_name", "HCMUTE - Nhắc Nhở & Hạn Chót")

        target_cal_id = gcal.get_or_create_calendar(cal_name)
        console.print(f"[green]✔ Lịch mục tiêu: '{cal_name}'[/green]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Đang đồng bộ {len(events)} sự kiện...", total=len(events))

            def progress_cb(current, total, title):
                progress.update(task, completed=current, description=f"Đang thêm: {title[:30]}...")

            created, updated, errors = gcal.sync_events(
                events,
                calendar_id=target_cal_id,
                callback=progress_cb,
            )

        console.print(
            Panel(
                f"[bold green]✔ Đồng bộ hoàn tất lên Google Calendar![/bold green]\n"
                f"➕ Thêm mới: {created} sự kiện\n"
                f"🔄 Cập nhật: {updated} sự kiện\n"
                f"⚠️ Lỗi: {errors} sự kiện\n"
                f"📅 Lịch: '{cal_name}' trên Google Calendar của bạn",
                title="Kết quả đồng bộ",
                border_style="green",
            )
        )

    except Exception as e:
        console.print(f"[bold red]❌ Lỗi khi đồng bộ Google Calendar:[/bold red] {e}")


@app.command()
def web(
    port: int = typer.Option(5000, "--port", "-p", help="Cổng chạy web"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host chạy web"),
):
    """Khởi chạy giao diện Web HCMUTE Sync."""
    from src.web_server import run_app
    run_app(host=host, port=port)


@app.command()
def export_ics(
    year: Optional[str] = typer.Option(None, "--year", "-y", help="Năm học"),
    term: Optional[str] = typer.Option(None, "--term", "-t", help="Học kỳ"),
    output: str = typer.Option("data/hcmute_schedule.ics", "--output", "-o", help="Đường dẫn file .ics xuất ra"),
):
    """Xuất toàn bộ TKB và Nhắc nhở hạn chót thành file .ics tiêu chuẩn."""
    print_banner()
    client = get_client()
    if not ensure_authenticated(client):
        return

    all_events = []
    with console.status("[bold cyan]Đang lấy dữ liệu từ portal HCMUTE..."):
        try:
            yt = client.get_year_and_term()
            curr_year = year or yt.get("CurrentYear") or yt.get("NamHienTai") or "2026-2027"
            curr_term = term or yt.get("CurrentTerm") or yt.get("HocKyHienTai") or "HK01"
        except Exception:
            curr_year = year or "2026-2027"
            curr_term = term or "HK01"

        try:
            raw_sched = client.get_schedule_semester(curr_year, curr_term)
            all_events.extend(parse_semester_schedule(raw_sched))
        except Exception as e:
            console.print(f"[yellow]Không lấy được TKB: {e}[/yellow]")

        try:
            all_raw_exams = []
            try:
                all_raw_exams.extend(client.get_exams_by_time())
            except Exception:
                pass
            try:
                all_raw_exams.extend(client.get_exam_schedule(curr_year, curr_term))
            except Exception:
                pass
            all_events.extend(parse_exam_schedule(all_raw_exams))
        except Exception:
            pass

        try:
            analyzer = NoticeAnalyzer(load_config())
            notices = client.get_general_announcements() + client.get_personal_messages()
            considered = analyzer.analyze_all_announcements(notices)
            for item in considered:
                ev = item.to_calendar_event()
                if ev:
                    all_events.append(ev)
        except Exception:
            pass

    if not all_events:
        console.print("[yellow]Không có sự kiện để xuất.[/yellow]")
        return

    out_file = export_events_to_ics(all_events, output_path=output)
    console.print(f"[bold green]✔ Đã xuất thành công {len(all_events)} sự kiện ra file:[/bold green] [cyan]{out_file}[/cyan]")


def interactive_menu():
    """Giao diện menu tương tác khi chạy công cụ không kèm tham số lệnh."""
    while True:
        print_banner()
        client = get_client()
        if client.is_authenticated():
            profile = client.get_user_profile() or {}
            name = profile.get("FullName") or profile.get("Username") or "Sinh viên"
            mssv = profile.get("Id") or ""
            console.print(f"[bold green]✔ Trạng thái:[/bold green] Đã đăng nhập: [bold cyan]{name}[/bold cyan] (MSSV: [bold yellow]{mssv}[/bold yellow])\n")
        else:
            console.print("[yellow]⚠️ Trạng thái: Chưa đăng nhập tài khoản HCMUTE.[/yellow]\n")

        menu_panel = Panel(
            "[bold cyan][1][/bold cyan] 🔄 Đồng bộ Toàn diện (TKB + Quét thông báo hạn chót + Google Calendar)\n"
            "[bold cyan][2][/bold cyan] 📢 Quét thông báo & Đánh giá hạn chót (Học phí, SHCD đầu khóa...)\n"
            "[bold cyan][3][/bold cyan] 📅 Xuất file lịch .ics (Import vào Google Calendar, iPhone, Outlook)\n"
            "[bold cyan][4][/bold cyan] 🌐 Mở Giao diện Web (Tùy chọn TKB, Lịch thi, Nhắc nhở)\n"
            "[bold cyan][5][/bold cyan] 🔐 Đăng nhập tài khoản HCMUTE\n"
            "[bold cyan][6][/bold cyan] 👤 Xem thông tin tài khoản\n"
            "[bold cyan][7][/bold cyan] 🚪 Đăng xuất\n"
            "[bold red][0][/bold red] ❌ Thoát chương trình",
            title="[bold yellow]MENU CHỨC NĂNG[/bold yellow]",
            border_style="cyan",
        )
        console.print(menu_panel)

        try:
            choice = typer.prompt("Vui lòng nhập lựa chọn của bạn (0-7)", default="4")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[cyan]Tạm biệt![/cyan]")
            break

        import re
        choice = re.sub(r"[^0-9]", "", str(choice))

        if choice == "1":
            sync(year=None, term=None, skip_notices=False, auto_remind=False)
        elif choice == "2":
            notices(auto_add=False, lookback_days=90)
        elif choice == "3":
            export_ics(year=None, term=None, output="data/hcmute_schedule.ics")
        elif choice == "4":
            web()
        elif choice == "5":
            login()
        elif choice == "6":
            status()
        elif choice == "7":
            logout()
        elif choice == "0":
            console.print("[cyan]Tạm biệt![/cyan]")
            break
        else:
            console.print("[red]Lựa chọn không hợp lệ, vui lòng chọn từ 0 đến 7.[/red]")

        console.print("\n" + "-" * 50 + "\n")
        try:
            input("Nhấn [Enter] để tiếp tục...")
        except (EOFError, KeyboardInterrupt):
            break
        console.clear()


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context):
    """Điểm vào mặc định: Nếu không truyền lệnh phụ, mở Menu tương tác."""
    if ctx.invoked_subcommand is None:
        interactive_menu()


if __name__ == "__main__":
    app()
