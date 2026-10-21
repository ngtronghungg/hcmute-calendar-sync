"""
Google Calendar API Integration
Manages OAuth2 authentication, calendar creation, and idempotent event syncing.
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.schedule_parser import CalendarEvent

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarSync:
    """Manages Google Calendar API interactions."""

    def __init__(
        self,
        credentials_path: str = "config/credentials.json",
        token_path: str = "data/google_token.json",
    ):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None

    def is_configured(self) -> bool:
        """Check if Google OAuth credentials file exists."""
        return os.path.exists(self.credentials_path) or os.path.exists(self.token_path)

    def authenticate(self, interactive: bool = True) -> bool:
        """
        Authenticate using OAuth2.
        Reads token from token_path if present and valid; otherwise initiates OAuth flow.
        """
        creds = None
        if os.path.exists(self.token_path):
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None

            if not creds:
                if not os.path.exists(self.credentials_path):
                    return False

                if not interactive:
                    return False

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            os.makedirs(os.path.dirname(self.token_path) or ".", exist_ok=True)
            with open(self.token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())

        self.service = build("calendar", "v3", credentials=creds)
        return True

    def get_or_create_calendar(self, calendar_name: str, description: str = "") -> str:
        """
        Find calendar by name or create a new dedicated calendar.
        Returns the calendarId.
        """
        if not self.service:
            raise RuntimeError("Google Calendar service chưa được khởi tạo.")

        calendar_list = self.service.calendarList().list().execute()
        for item in calendar_list.get("items", []):
            if item.get("summary") == calendar_name:
                return item["id"]

        # Create new secondary calendar
        new_cal = {
            "summary": calendar_name,
            "description": description or f"Lịch tự động đồng bộ từ online.hcmute.edu.vn",
            "timeZone": "Asia/Ho_Chi_Minh",
        }
        created = self.service.calendars().insert(body=new_cal).execute()
        return created["id"]

    def _build_google_event_payload(
        self,
        event: CalendarEvent,
        reminder_minutes: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Convert CalendarEvent to Google Calendar API event body."""
        body = {
            "summary": event.title,
            "location": event.location,
            "description": event.description,
            "start": {
                "dateTime": event.start_datetime.isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "end": {
                "dateTime": event.end_datetime.isoformat(),
                "timeZone": "Asia/Ho_Chi_Minh",
            },
            "iCalUID": f"{event.uid}@hcmute.edu.vn",
            "extendedProperties": {
                "private": {
                    "hcmute_uid": event.uid,
                    "event_type": event.event_type,
                    "category": event.category,
                }
            },
        }

        # Color codes: 11=Red (Deadline), 5=Yellow (Class), 4=Flamingo/Orange (Exam)
        if event.event_type == "DEADLINE":
            body["colorId"] = "11"  # Tomato/Red
        elif event.event_type == "EXAM":
            body["colorId"] = "4"   # Flamingo
        elif event.event_type == "CLASS":
            body["colorId"] = "9"   # Blueberry

        # Reminders
        if reminder_minutes:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in reminder_minutes],
            }
        else:
            if event.event_type == "CLASS":
                body["reminders"] = {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": 20}],
                }
            elif event.event_type in ("DEADLINE", "EXAM"):
                body["reminders"] = {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 1440},  # 1 day
                        {"method": "popup", "minutes": 120},   # 2 hours
                    ],
                }

        return body

    def sync_events(
        self,
        events: List[CalendarEvent],
        calendar_id: str = "primary",
        reminder_minutes: Optional[List[int]] = None,
        callback: Optional[Any] = None,
    ) -> Tuple[int, int, int]:
        """
        Sync list of events to Google Calendar.
        Returns: (created_count, updated_count, error_count)
        """
        if not self.service:
            raise RuntimeError("Chưa xác thực Google Calendar.")

        created_count = 0
        updated_count = 0
        error_count = 0

        # Retrieve existing events in calendar by querying private extendedProperties
        for idx, event in enumerate(events):
            try:
                event_body = self._build_google_event_payload(event, reminder_minutes)

                # Search if event already exists
                search_res = (
                    self.service.events()
                    .list(
                        calendarId=calendar_id,
                        privateExtendedProperty=f"hcmute_uid={event.uid}",
                        maxResults=1,
                    )
                    .execute()
                )

                items = search_res.get("items", [])
                if items:
                    existing_id = items[0]["id"]
                    self.service.events().update(
                        calendarId=calendar_id,
                        eventId=existing_id,
                        body=event_body,
                    ).execute()
                    updated_count += 1
                else:
                    self.service.events().insert(
                        calendarId=calendar_id,
                        body=event_body,
                    ).execute()
                    created_count += 1

                if callback:
                    callback(idx + 1, len(events), event.title)

            except HttpError as err:
                error_count += 1
                # If error is due to duplicate iCalUID, try update
                if err.resp.status == 409:
                    try:
                        self.service.events().import_(
                            calendarId=calendar_id,
                            body=event_body,
                        ).execute()
                        updated_count += 1
                        error_count -= 1
                    except Exception:
                        pass
            except Exception:
                error_count += 1

        return created_count, updated_count, error_count
