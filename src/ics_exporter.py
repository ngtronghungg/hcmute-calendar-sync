"""
iCalendar (.ics) Exporter
Exports calendar events to standard .ics format for instant import into Google Calendar,
Apple Calendar, Microsoft Outlook, etc.
"""

import os
import zoneinfo
from datetime import datetime, timezone, timedelta
from typing import List
from icalendar import Calendar, Event as ICalEvent, Alarm, Timezone, TimezoneStandard
from src.schedule_parser import CalendarEvent

VIETNAM_TZ = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")


def create_ical_calendar(
    events: List[CalendarEvent],
    calendar_name: str = "HCMUTE Schedule & Reminders",
    add_reminders: bool = True,
) -> Calendar:
    """Create an icalendar.Calendar object strictly pinned to Asia/Ho_Chi_Minh (UTC+7)."""
    cal = Calendar()
    cal.add("prodid", "-//HCMUTE Calendar Sync//VN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)
    cal.add("x-wr-timezone", "Asia/Ho_Chi_Minh")

    # RFC 5545 VTIMEZONE component for Asia/Ho_Chi_Minh
    vtz = Timezone()
    vtz.add("tzid", "Asia/Ho_Chi_Minh")
    vtz_std = TimezoneStandard()
    vtz_std.add("dtstart", datetime(1970, 1, 1, 0, 0, 0))
    vtz_std.add("tzoffsetfrom", timedelta(hours=7))
    vtz_std.add("tzoffsetto", timedelta(hours=7))
    vtz_std.add("tzname", "+07")
    vtz.add_component(vtz_std)
    cal.add_component(vtz)

    import re
    from src.schedule_parser import assign_course_colors, get_course_color
    assign_course_colors(events)

    for ev in events:
        ie = ICalEvent()
        ie.add("summary", ev.title)
        ie.add("uid", f"{ev.uid}@hcmute.edu.vn")
        ie.add("dtstamp", datetime.now(timezone.utc))

        # Explicit Vietnam Timezone (UTC+7)
        st_vn = ev.start_datetime.replace(tzinfo=VIETNAM_TZ) if ev.start_datetime.tzinfo is None else ev.start_datetime
        en_vn = ev.end_datetime.replace(tzinfo=VIETNAM_TZ) if ev.end_datetime.tzinfo is None else ev.end_datetime

        ie.add("dtstart", st_vn)
        ie.add("dtend", en_vn)

        if ev.location:
            ie.add("location", ev.location)
        if ev.description:
            ie.add("description", ev.description)

        ev_type = (ev.event_type or "").upper()
        # Set category, priority & distinct color (RFC 7986 / Apple Calendar)
        if ev_type == "EXAM":
            ie.add("color", "#d50000")  # Bright Red for exams
            ie.add("x-apple-calendar-color", "#d50000")
            ie.add("priority", 1)  # Highest priority
            ie.add("categories", ["LỊCH THI", "EXAM", "QUAN TRỌNG", "HCMUTE"])
        elif ev_type == "DEADLINE":
            ie.add("color", "#e67c73")
            ie.add("x-apple-calendar-color", "#e67c73")
            ie.add("priority", 2)
            ie.add("categories", ["NHẮC NHỞ", "DEADLINE", "HCMUTE"])
        else:
            hex_c = getattr(ev, "color_hex", "") or get_course_color(ev.title)[1]
            ie.add("color", hex_c)
            ie.add("x-apple-calendar-color", hex_c)
            ie.add("priority", 5)
            clean_sub = re.sub(r"\[.*?\]|\(.*?\)", "", ev.title).strip()
            ie.add("categories", [clean_sub or "THỜI KHÓA BIỂU", "CLASS", "HCMUTE"])

        # Configure alarms
        if add_reminders:
            if ev.event_type == "CLASS":
                alarm = Alarm()
                alarm.add("action", "DISPLAY")
                alarm.add("description", f"Sắp đến giờ học: {ev.title}")
                alarm.add("trigger", timedelta(minutes=-20))
                ie.add_component(alarm)
            elif ev.event_type in ("DEADLINE", "EXAM"):
                alarm1 = Alarm()
                alarm1.add("action", "DISPLAY")
                alarm1.add("description", f"Hạn chót ngày mai: {ev.title}")
                alarm1.add("trigger", timedelta(days=-1))
                ie.add_component(alarm1)

                alarm2 = Alarm()
                alarm2.add("action", "DISPLAY")
                alarm2.add("description", f"HẠN CHÓT SẮP HẾT: {ev.title}")
                alarm2.add("trigger", timedelta(hours=-2))
                ie.add_component(alarm2)

        cal.add_component(ie)

    return cal


def events_to_ics_bytes(
    events: List[CalendarEvent],
    calendar_name: str = "HCMUTE Schedule & Reminders",
    add_reminders: bool = True,
) -> bytes:
    """Generate iCalendar bytes directly in memory."""
    cal = create_ical_calendar(events, calendar_name=calendar_name, add_reminders=add_reminders)
    return cal.to_ical()


def export_events_to_ics(
    events: List[CalendarEvent],
    output_path: str = "data/hcmute_schedule.ics",
    calendar_name: str = "HCMUTE Schedule & Reminders",
    add_reminders: bool = True,
) -> str:
    """
    Export a list of CalendarEvent objects to an iCalendar (.ics) file.
    """
    cal = create_ical_calendar(events, calendar_name=calendar_name, add_reminders=add_reminders)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(cal.to_ical())

    return os.path.abspath(output_path)
