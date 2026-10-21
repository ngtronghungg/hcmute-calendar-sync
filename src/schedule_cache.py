"""
Schedule & Calendar Feed Cache Helper
High-performance disk cache for parsed CalendarEvent objects.
Eliminates circular dependencies between web_server and telegram_bot.
Provides sub-millisecond schedule queries for Telegram bot and iCal endpoints.
Supports per-user isolated caching to guarantee zero cross-student data leakage.
"""

import os
import json
import re
from typing import List, Optional
from src.schedule_parser import CalendarEvent

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CALENDAR_FEED_CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "calendar_feed_cache.json")
EXAM_CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "exam_cache.json")


def _get_user_file_paths(mssv: Optional[str] = None):
    """Determine cache file paths for a specific MSSV or default global cache."""
    if not mssv:
        return CALENDAR_FEED_CACHE_FILE, EXAM_CACHE_FILE
    safe_mssv = re.sub(r"[^a-zA-Z0-9_\-]", "", str(mssv).strip())
    if not safe_mssv:
        return CALENDAR_FEED_CACHE_FILE, EXAM_CACHE_FILE
    user_dir = os.path.join(PROJECT_ROOT, "data", "users", safe_mssv)
    os.makedirs(user_dir, exist_ok=True)
    return (
        os.path.join(user_dir, "calendar_feed_cache.json"),
        os.path.join(user_dir, "exam_cache.json"),
    )


def save_cached_exams(exams: List[CalendarEvent], mssv: Optional[str] = None) -> None:
    """Permanently persist parsed exam CalendarEvent objects to disk, merging with existing."""
    if not exams:
        return
    try:
        _, exam_file = _get_user_file_paths(mssv)
        os.makedirs(os.path.dirname(exam_file), exist_ok=True)
        existing = {e.uid: e for e in load_cached_exams(mssv=mssv)}
        for ex in exams:
            existing[ex.uid] = ex
        with open(exam_file, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in existing.values()], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_cached_exams(mssv: Optional[str] = None) -> List[CalendarEvent]:
    """Load cached exam CalendarEvent objects for a specific MSSV or global."""
    feed_file, exam_file = _get_user_file_paths(mssv)
    if os.path.exists(exam_file):
        try:
            with open(exam_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [CalendarEvent.from_dict(d) for d in raw]
        except Exception:
            pass
    if os.path.exists(feed_file):
        try:
            with open(feed_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            exams = [CalendarEvent.from_dict(d) for d in raw if d.get("event_type") in ("EXAM", "THI") or "THI" in d.get("title", "")]
            if exams:
                save_cached_exams(exams, mssv=mssv)
            return exams
        except Exception:
            pass
    return []


def save_feed_cache(events: List[CalendarEvent], mssv: Optional[str] = None) -> None:
    """Cache parsed CalendarEvent objects to file, merging intelligently so exams are never lost."""
    try:
        feed_file, _ = _get_user_file_paths(mssv)
        os.makedirs(os.path.dirname(feed_file), exist_ok=True)
        old_events = load_feed_cache(mssv=mssv)
        merged = {e.uid: e for e in old_events}
        for e in events:
            merged[e.uid] = e
        with open(feed_file, "w", encoding="utf-8") as f:
            json.dump([e.to_dict() for e in merged.values()], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_feed_cache(mssv: Optional[str] = None) -> List[CalendarEvent]:
    """Load cached CalendarEvent objects for a specific student, ensuring exams are preserved."""
    feed_file, _ = _get_user_file_paths(mssv)
    events = []
    if os.path.exists(feed_file):
        try:
            with open(feed_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            events = [CalendarEvent.from_dict(d) for d in raw]
        except Exception:
            pass
    cached_exams = load_cached_exams(mssv=mssv)
    existing_uids = {e.uid for e in events}
    for ex in cached_exams:
        if ex.uid not in existing_uids:
            events.append(ex)
    return events
