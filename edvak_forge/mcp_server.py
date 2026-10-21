"""MCP server exposing bounded clinic operations over synthetic JSON data.
Install requirements and run: python mcp_server.py
"""
from datetime import date, datetime
from pathlib import Path
import secrets

from mcp.server.fastmcp import FastMCP

from agent import (
    load_data,
    check_refill_eligibility as _check_refill,
    find_free_slots as _find_slots,
)

mcp = FastMCP("maplewood-clinic")
DATA = Path(__file__).parent / "data"
state = load_data(DATA)
verification_tokens = {}



@mcp.tool()
def find_patient(name: str = "", contact: str = "", date_of_birth: str = "") -> dict:
    """Verify identity and issue a server-generated verification token."""
    patients = state["patients"]
    matched_patient = None
    method = None

    if contact:
        contact_value = contact.strip().lower()

        for patient in patients:
            patient_contacts = {
                patient.get("phone", "").strip().lower(),
                patient.get("email", "").strip().lower(),
            }

            if contact_value and contact_value in patient_contacts:
                matched_patient = patient
                method = "on_file_contact"
                break

            caregiver = patient.get("authorised_caregiver", {})
            caregiver_phone = caregiver.get("phone", "").strip().lower()

            if caregiver_phone and contact_value == caregiver_phone:
                matched_patient = patient
                method = "authorised_caregiver_contact"
                break

    if matched_patient is None and name and date_of_birth:
        normalized_name = name.strip().lower()
        normalized_dob = date_of_birth.strip()

        for patient in patients:
            if (
                patient.get("name", "").strip().lower() == normalized_name
                and patient.get("dob") == normalized_dob
            ):
                matched_patient = patient
                method = "name_and_dob"
                break

    if matched_patient is None:
        return {
            "patient_id": None,
            "verified": False,
            "reason": "No exact verification match",
        }

    token = secrets.token_urlsafe(32)
    verification_tokens[token] = matched_patient["id"]

    return {
        "patient_id": matched_patient["id"],
        "verified": True,
        "verification_method": method,
        "verification_token": token,
    }




@mcp.tool()
def get_patient_medications(
    patient_id: str,
    verification_token: str
) -> dict:
    """Return medications only with a valid server-issued verification token."""
    patient_id = patient_id.strip()
    verification_token = verification_token.strip()

    if not verification_token or verification_tokens.get(verification_token) != patient_id:
        return {
            "success": False,
            "reason": "identity_verification_required",
            "medications": []
        }

    if not any(p["id"] == patient_id for p in state["patients"]):
        return {
            "success": False,
            "reason": "patient_not_found",
            "medications": []
        }

    medications = [
        {
            "medication": medication["medication"],
            "controlled_schedule": medication.get("controlled_schedule"),
            "days_supply": medication["days_supply"],
            "last_filled": medication["last_filled"],
            "refills_remaining": medication["refills_remaining"],
            "prescriber": medication["prescriber"],
        }
        for medication in state["medications"]
        if medication["patient_id"] == patient_id
    ]

    return {
        "success": True,
        "medications": medications
    }


@mcp.tool()
def check_refill_eligibility(patient_id: str, medication_name: str) -> dict:
    """Check clinic refill policy; this tool never approves or sends a prescription."""
    patient_id = patient_id.strip()
    medication_name = medication_name.strip()

    patient = next(
        (item for item in state["patients"] if item["id"] == patient_id),
        None,
    )
    if not patient:
        return {"eligible": False, "reason": "patient_not_found"}

    medication = next(
        (
            item
            for item in state["medications"]
            if item["patient_id"] == patient_id
            and medication_name.lower() in item["medication"].lower()
        ),
        None,
    )
    return _check_refill(patient, medication, state["clinic"])


@mcp.tool()
def find_free_appointment_slots(
    provider_id: str,
    visit_type: str,
    start_date: str,
    end_date: str,
    after: str = "09:00",
    before: str = "17:00",
    limit: int = 5,
) -> list:
    """Find slots respecting clinic hours, holidays, provider leave, and conflicts."""
    return _find_slots(
        state["clinic"],
        state["appointments"],
        provider_id.strip(),
        visit_type.strip(),
        start_date.strip(),
        end_date.strip(),
        after.strip(),
        before.strip(),
        limit,
    )


@mcp.tool()
def book_appointment(
    patient_id: str,
    provider_id: str,
    visit_type: str,
    appointment_date: str,
    start: str,
) -> dict:
    """Book an available slot after identity verification and slot validation."""
    patient_id = patient_id.strip()
    provider_id = provider_id.strip()
    visit_type = visit_type.strip()
    appointment_date = appointment_date.strip()
    start = start.strip()

    try:
        date.fromisoformat(appointment_date)
        start = datetime.strptime(start, "%H:%M").strftime("%H:%M")
    except ValueError:
        return {"success": False, "reason": "invalid_date_or_time"}

    if not any(patient["id"] == patient_id for patient in state["patients"]):
        return {"success": False, "reason": "patient_not_found"}

    slots = _find_slots(
        state["clinic"],
        state["appointments"],
        provider_id,
        visit_type,
        appointment_date,
        appointment_date,
        after=start,
        before="17:00",
        limit=50,
    )
    slot = next(
        (
            item
            for item in slots
            if item["date"] == appointment_date and item["start"] == start
        ),
        None,
    )
    if not slot:
        return {"success": False, "reason": "slot_unavailable"}

    appointment_number = len(state["appointments"]) + 1
    appointment = {
        "id": f"NEW{appointment_number:03d}",
        "patient_id": patient_id,
        "provider": provider_id,
        "date": appointment_date,
        "start": slot["start"],
        "end": slot["end"],
        "visit_type": visit_type,
        "status": "booked",
    }
    state["appointments"].append(appointment)
    return {"success": True, "appointment": appointment}


@mcp.tool()
def cancel_appointment(appointment_id: str, patient_id: str) -> dict:
    """Cancel a booked appointment only when it belongs to the supplied patient ID."""
    appointment_id = appointment_id.strip()
    patient_id = patient_id.strip()

    for appointment in state["appointments"]:
        if (
            appointment["id"] == appointment_id
            and appointment["patient_id"] == patient_id
            and appointment["status"] == "booked"
        ):
            appointment["status"] = "cancelled"
            return {
                "success": True,
                "appointment_id": appointment_id,
                "status": "cancelled",
            }

    return {"success": False, "reason": "booking_not_found_or_not_owned"}


@mcp.tool()
def reschedule_appointment(
    appointment_id: str,
    patient_id: str,
    provider_id: str,
    visit_type: str,
    new_date: str,
    new_start: str,
) -> dict:
    """Reschedule an owned appointment only when the exact requested slot is free."""
    appointment_id = appointment_id.strip()
    patient_id = patient_id.strip()
    provider_id = provider_id.strip()
    visit_type = visit_type.strip()
    new_date = new_date.strip()
    new_start = new_start.strip()

    try:
        date.fromisoformat(new_date)
        new_start = datetime.strptime(new_start, "%H:%M").strftime("%H:%M")
    except ValueError:
        return {"success": False, "reason": "invalid_date_or_time"}

    original = next(
        (
            appointment
            for appointment in state["appointments"]
            if appointment["id"] == appointment_id
            and appointment["patient_id"] == patient_id
            and appointment["status"] == "booked"
        ),
        None,
    )
    if not original:
        return {"success": False, "reason": "booking_not_found_or_not_owned"}

    # Keep the original booked while checking the requested replacement slot.
    slots = _find_slots(
        state["clinic"],
        state["appointments"],
        provider_id,
        visit_type,
        new_date,
        new_date,
        after=new_start,
        before="17:00",
        limit=50,
    )
    slot = next(
        (
            item
            for item in slots
            if item["date"] == new_date and item["start"] == new_start
        ),
        None,
    )
    if not slot:
        return {"success": False, "reason": "slot_unavailable"}

    original["status"] = "cancelled"
    appointment = {
        "id": f"NEW{len(state['appointments']) + 1:03d}",
        "patient_id": patient_id,
        "provider": provider_id,
        "date": new_date,
        "start": slot["start"],
        "end": slot["end"],
        "visit_type": visit_type,
        "status": "booked",
    }
    state["appointments"].append(appointment)

    return {
        "success": True,
        "cancelled_appointment_id": appointment_id,
        "new_appointment": appointment,
    }


@mcp.tool()
def route_to_human_queue(message_id: str, queue: str, reason: str) -> dict:
    """Route a message to one of the configured clinic queues."""
    allowed_queues = set(state["clinic"]["routing_queues"])
    if queue not in allowed_queues:
        return {
            "success": False,
            "reason": "invalid_queue",
            "allowed": sorted(allowed_queues),
        }

    return {
        "success": True,
        "message_id": message_id,
        "queue": queue,
        "reason": reason,
        "status": "queued_for_this_run",
    }



@mcp.tool()
def draft_reply(message_id: str, reply_text: str, urgency: str = "routine") -> dict:
    """Draft, but never send, a patient-facing reply."""

    message_id = message_id.strip()
    reply_text = reply_text.strip()
    urgency = urgency.strip().lower()

    allowed_urgencies = {"routine", "urgent", "emergency"}

    if urgency not in allowed_urgencies:
        return {
            "success": False,
            "reason": "invalid_urgency",
            "allowed": sorted(allowed_urgencies)
        }

    if not message_id or not reply_text:
        return {
            "success": False,
            "reason": "missing_message_id_or_reply_text"
        }

    if urgency == "emergency":
        text_lower = reply_text.lower()

        has_emergency_direction = (
            "911" in text_lower
            or "emergency department" in text_lower
        )
        clinic_alerted = "clinic has been alerted" in text_lower

        if not (has_emergency_direction and clinic_alerted):
            return {
                "success": False,
                "reason": "emergency_reply_policy_violation"
            }

    return {
        "success": True,
        "message_id": message_id,
        "reply_draft": reply_text,
        "sent": False
    }


if __name__ == "__main__":
    mcp.run()
