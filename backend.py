# Step1: Import Google Calendar Service and dependencies

import logging
import datetime as dt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from google_calendar_service import GoogleCalendarService

# Setup logger for backend
logger = logging.getLogger("backend")
logging.basicConfig(level=logging.INFO)

# Instantiate Google Calendar Service
try:
    calendar_service = GoogleCalendarService()
except Exception as e:
    logger.error(f"Failed to initialize GoogleCalendarService on backend startup: {e}")
    # We let it pass so the server can start up and return proper errors if configuration is fixed later,
    # or raise here. Raising here prevents starting the server with misconfigured env vars.
    calendar_service = None

# Step2: Create Data Contracts/Ensure data validation using Pydantic Models

class AppointmentRequest(BaseModel):
    patient_name: str
    reason: str | None = None
    start_time: dt.datetime
    date: dt.date


class AppointmentResponse(BaseModel):
    id: int
    patient_name: str
    reason: str | None
    start_time: dt.datetime
    date: dt.date
    canceled: bool
    created_at: dt.datetime


class CancelAppointmentRequest(BaseModel):
    patient_name: str
    start_time: dt.datetime | None = None
    date: dt.date


class CancelAppointmentResponse(BaseModel):
    canceled_count: int


class ListAppointmentRequest(BaseModel):
    date: dt.date


# Step3: Create FastAPI application and endpoints

app = FastAPI()

def check_service_initialized():
    global calendar_service
    if calendar_service is None:
        try:
            calendar_service = GoogleCalendarService()
        except Exception as e:
            logger.error(f"On-demand initialization of GoogleCalendarService failed: {e}")
            raise HTTPException(
                status_code=500,
                detail="Google Calendar Service is not initialized. Check server logs and credentials."
            )

# book appt
@app.post("/book_appointment", response_model=AppointmentResponse)
def book_appointment(request: AppointmentRequest):
    check_service_initialized()
    try:
        # if somehow string comes instead of datetime
        if isinstance(request.start_time, str):
            request.start_time = dt.datetime.fromisoformat(request.start_time)
    except:
        raise HTTPException(status_code=400, detail="Invalid datetime format. Use ISO format.")

    try:
        # Book appointment using Google Calendar Service
        new_appt = calendar_service.create_appointment(
            patient_name=request.patient_name,
            reason=request.reason,
            start_time=request.start_time,
            date=request.date
        )
        return AppointmentResponse(**new_appt)
    except ValueError as e:
        if str(e) == "Time slot already booked":
            raise HTTPException(status_code=400, detail="Time slot already booked")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error booking appointment: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to book appointment on Google Calendar: {str(e)}"
        )


@app.post("/cancel_appointment", response_model=CancelAppointmentResponse)
def cancel_appointment(request: CancelAppointmentRequest):
    check_service_initialized()
    try:
        canceled_count = calendar_service.cancel_appointments(
            patient_name=request.patient_name,
            date=request.date,
            start_time=request.start_time
        )

        if canceled_count == 0:
            if request.start_time is not None:
                raise HTTPException(
                    status_code=404,
                    detail="No matching appointment found",
                )
            else:
                raise HTTPException(
                    status_code=404,
                    detail="No appointments found for this date",
                )

        return CancelAppointmentResponse(canceled_count=canceled_count)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling appointment: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel appointment on Google Calendar: {str(e)}"
        )


# list appt
@app.post("/list_appointments/", response_model=list[AppointmentResponse])
def list_appointments(request: ListAppointmentRequest):
    check_service_initialized()
    try:
        booked_appointments = calendar_service.list_appointments(request.date)
        return [AppointmentResponse(**appt) for appt in booked_appointments]
    except Exception as e:
        logger.error(f"Error listing appointments: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list appointments from Google Calendar: {str(e)}"
        )


if __name__ == "__main__":
    uvicorn.run("backend:app", host="127.0.0.1", port=4444, reload=True)

