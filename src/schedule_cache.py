"""
Schedule & Calendar Feed Cache Helper
High-performance disk cache for parsed CalendarEvent objects.
Eliminates circular dependencies between web_server and telegram_bot.
Provides sub-millisecond schedule queries for Telegram bot and iCal endpoints.
"""

import os
import json
from typing import List
from src.schedule_parser import CalendarEvent

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CALENDAR_FEED_CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "calendar_feed_cache.json")
EXAM_CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "exam_cache.json")


def save_cached_exams(exams: List[CalendarEvent]) -> None:
    """Permanently persist parsed exam CalendarEvent objects to disk, merging with existing."""
    if not exams:
        return
    try:
        os.makedirs(os.path.dirname(EXAM_CACHE_FILE), exist_ok=True)
        existing = {e.uid: e for e in load_cached_exams()}
        for ex in exams:
            existing[ex.uid] = ex
        with open(EXAM_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in existing.values()], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_cached_exams() -> List[CalendarEvent]:
    """Load cached exam CalendarEvent objects."""
    if os.path.exists(EXAM_CACHE_FILE):
        try:
            with open(EXAM_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [CalendarEvent.from_dict(d) for d in raw]
        except Exception:
            pass
    if os.path.exists(CALENDAR_FEED_CACHE_FILE):
        try:
            with open(CALENDAR_FEED_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            exams = [CalendarEvent.from_dict(d) for d in raw if d.get("event_type") in ("EXAM", "THI") or "THI" in d.get("title", "")]
            if exams:
                save_cached_exams(exams)
            return exams
        except Exception:
            pass
    return []


def save_feed_cache(events: List[CalendarEvent]) -> None:
    """Cache parsed CalendarEvent objects to file, merging intelligently so exams are never lost."""
    try:
        os.makedirs(os.path.dirname(CALENDAR_FEED_CACHE_FILE), exist_ok=True)
        old_events = load_feed_cache()
        merged = {e.uid: e for e in old_events}
        for e in events:
            merged[e.uid] = e
        with open(CALENDAR_FEED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in merged.values()], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_feed_cache() -> List[CalendarEvent]:
    """Load cached CalendarEvent objects, ensuring exams are preserved."""
    events = []
    if os.path.exists(CALENDAR_FEED_CACHE_FILE):
        try:
            with open(CALENDAR_FEED_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            events = [CalendarEvent.from_dict(d) for d in raw]
        except Exception:
            pass
    cached_exams = load_cached_exams()
    existing_uids = {e.uid for e in events}
    for ex in cached_exams:
        if ex.uid not in existing_uids:
            events.append(ex)
    return events
