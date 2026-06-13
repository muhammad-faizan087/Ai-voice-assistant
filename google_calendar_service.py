import datetime as dt
import os
import logging
import hashlib
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logger = logging.getLogger("google_calendar_service")
if not logger.hasHandlers():
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

class GoogleCalendarService:
    def __init__(self):
        self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
        
        # Validations for calendar ID
        if not self.calendar_id:
            logger.error("GOOGLE_CALENDAR_ID is not set in environment variables.")
            raise ValueError("GOOGLE_CALENDAR_ID is not set in environment variables.")
            
        credentials_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
        credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH")
        
        try:
            if credentials_json_str:
                logger.info("Loading Google Calendar credentials from GOOGLE_CREDENTIALS_JSON environment variable.")
                info = json.loads(credentials_json_str)
                self.creds = service_account.Credentials.from_service_account_info(
                    info,
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
            elif credentials_path:
                logger.info(f"Loading Google Calendar credentials from file: {credentials_path}")
                if not os.path.exists(credentials_path):
                    logger.error(f"Google credentials file not found at: {credentials_path}")
                    raise FileNotFoundError(f"Google credentials file not found at: {credentials_path}")
                self.creds = service_account.Credentials.from_service_account_file(
                    credentials_path,
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
            else:
                logger.error("Neither GOOGLE_CREDENTIALS_JSON nor GOOGLE_CREDENTIALS_PATH is set in environment variables.")
                raise ValueError("Neither GOOGLE_CREDENTIALS_JSON nor GOOGLE_CREDENTIALS_PATH is set in environment variables.")
                
            self.service = build('calendar', 'v3', credentials=self.creds)
            logger.info("Successfully authenticated and connected to Google Calendar API.")
        except Exception as e:
            logger.error(f"Failed to authenticate or build Google Calendar client: {e}")
            raise RuntimeError(f"Authentication with Google Calendar failed: {e}")

    def _event_id_to_int(self, event_id: str) -> int:
        """Generates a stable 32-bit positive integer from a string event ID."""
        h = hashlib.sha256(event_id.encode('utf-8')).hexdigest()
        return int(h, 16) % 2147483647

    def _map_event_to_dict(self, event: dict) -> dict:
        """Maps Google Calendar event dict to a unified representation."""
        event_id = event.get('id', '')
        numeric_id = self._event_id_to_int(event_id)
        
        patient_name = event.get('summary', '')
        reason = event.get('description', '')
        
        start_data = event.get('start', {})
        start_time_str = start_data.get('dateTime') or start_data.get('date')
        
        # Parse ISO datetime
        # Replace 'Z' with '+00:00' for universal python datetime parsing compatibility
        normalized_start = start_time_str.replace('Z', '+00:00') if start_time_str else None
        start_time = dt.datetime.fromisoformat(normalized_start) if normalized_start else None
        
        # If timezone-aware, standardise to naive/UTC or keep as is.
        # We will keep it timezone-aware to preserve exact values.
        appointment_date = start_time.date() if start_time else None
        
        canceled = event.get('status') == 'cancelled'
        
        created_str = event.get('created')
        if created_str:
            created_at = dt.datetime.fromisoformat(created_str.replace('Z', '+00:00'))
        else:
            created_at = dt.datetime.now(dt.timezone.utc)
            
        return {
            "id": numeric_id,
            "patient_name": patient_name,
            "reason": reason if reason else None,
            "start_time": start_time,
            "date": appointment_date,
            "canceled": canceled,
            "created_at": created_at,
            "event_id": event_id
        }

    def check_availability(self, start_time: dt.datetime, duration_minutes: int = 30) -> bool:
        """
        Queries Google Calendar events to verify if the requested slot overlaps with any active booking.
        Returns True if available, False if occupied.
        """
        end_time = start_time + dt.timedelta(minutes=duration_minutes)
        
        # Ensure UTC timezone suffix for API if datetime is naive
        time_min = start_time.isoformat() + 'Z' if start_time.tzinfo is None else start_time.isoformat()
        time_max = end_time.isoformat() + 'Z' if end_time.tzinfo is None else end_time.isoformat()
        
        logger.info(f"Checking availability: {time_min} to {time_max}")
        
        try:
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            # Filter out cancelled events
            active_events = [e for e in events if e.get('status') != 'cancelled']
            
            if active_events:
                logger.info(f"Slot {time_min} - {time_max} is occupied by {len(active_events)} event(s).")
                return False
                
            logger.info(f"Slot {time_min} - {time_max} is available.")
            return True
        except HttpError as e:
            logger.error(f"Google Calendar API error in check_availability: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error checking availability: {e}")
            raise

    def create_appointment(
        self,
        patient_name: str,
        reason: str | None,
        start_time: dt.datetime,
        date: dt.date,
        duration_minutes: int = 30
    ) -> dict:
        """
        Creates a new event in Google Calendar after verifying availability.
        Returns the mapped event details dict.
        """
        # Align the date component of start_time with the requested date
        start_time = dt.datetime.combine(date, start_time.time()).replace(tzinfo=start_time.tzinfo)

        # 1. Double check availability
        if not self.check_availability(start_time, duration_minutes):
            raise ValueError("Time slot already booked")
            
        end_time = start_time + dt.timedelta(minutes=duration_minutes)
        time_min = start_time.isoformat() + 'Z' if start_time.tzinfo is None else start_time.isoformat()
        time_max = end_time.isoformat() + 'Z' if end_time.tzinfo is None else end_time.isoformat()
        
        body = {
            'summary': patient_name,
            'description': reason or '',
            'start': {
                'dateTime': time_min,
            },
            'end': {
                'dateTime': time_max,
            },
            'extendedProperties': {
                'private': {
                    'patient_name': patient_name,
                    'reason': reason or '',
                    'date': date.isoformat()
                }
            }
        }
        
        if start_time.tzinfo is None:
            body['start']['timeZone'] = 'UTC'
            body['end']['timeZone'] = 'UTC'
            
        logger.info(f"Booking appointment for '{patient_name}' on {date} at {start_time}")
        
        try:
            event = self.service.events().insert(
                calendarId=self.calendar_id,
                body=body
            ).execute()
            logger.info(f"Successfully booked appointment. Event ID: {event.get('id')}")
            return self._map_event_to_dict(event)
        except HttpError as e:
            logger.error(f"Google Calendar API error in create_appointment: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error booking appointment: {e}")
            raise

    def list_appointments(self, date: dt.date) -> list[dict]:
        """
        Lists all active appointments for a specific date.
        """
        start_dt = dt.datetime.combine(date, dt.time.min)
        end_dt = start_dt + dt.timedelta(days=1)
        
        time_min = start_dt.isoformat() + 'Z'
        time_max = end_dt.isoformat() + 'Z'
        
        logger.info(f"Listing appointments for date: {date}")
        
        try:
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            appointments = []
            
            for event in events:
                if event.get('status') == 'cancelled':
                    continue
                appointments.append(self._map_event_to_dict(event))
                
            # Sort by start_time ascending
            appointments.sort(key=lambda x: x['start_time'])
            return appointments
        except HttpError as e:
            logger.error(f"Google Calendar API error in list_appointments: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error listing appointments: {e}")
            raise

    def cancel_appointments(
        self,
        patient_name: str,
        date: dt.date,
        start_time: dt.datetime | None = None
    ) -> int:
        """
        Locates and deletes event(s) matching patient_name, date, and optional start_time.
        Returns the count of successfully deleted appointments.
        """
        # Align start_time's date with the target date parameter if provided
        if start_time is not None:
            start_time = dt.datetime.combine(date, start_time.time()).replace(tzinfo=start_time.tzinfo)

        # Get all appointments for that day
        day_appointments = self.list_appointments(date)
        
        matching_event_ids = []
        for appt in day_appointments:
            # Case-sensitive exact match like PostgreSQL database query
            name_match = appt['patient_name'] == patient_name
            
            # Start time match (if provided)
            time_match = True
            if start_time is not None:
                # Compare naive components of datetime to handle potential timezone mismatch
                req_naive = start_time.replace(tzinfo=None) if start_time.tzinfo is not None else start_time
                appt_naive = appt['start_time'].replace(tzinfo=None) if appt['start_time'].tzinfo is not None else appt['start_time']
                time_match = req_naive == appt_naive
                
            if name_match and time_match:
                matching_event_ids.append(appt['event_id'])
                
        if not matching_event_ids:
            logger.warning(f"No appointments found matching patient={patient_name}, date={date}, start_time={start_time}")
            return 0
            
        canceled_count = 0
        for event_id in matching_event_ids:
            try:
                logger.info(f"Deleting calendar event: {event_id}")
                self.service.events().delete(
                    calendarId=self.calendar_id,
                    eventId=event_id
                ).execute()
                canceled_count += 1
            except HttpError as e:
                logger.error(f"Failed to delete event {event_id}: {e}")
                raise
                
        return canceled_count
