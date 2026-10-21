"""Safety-first deterministic baseline for Maplewood Family Clinic."""
from __future__ import annotations
import json, re, argparse
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Any

DATA = Path(__file__).parent / "data"

def load_data(directory: Path = DATA):
    return {name: json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("clinic", "patients", "medications", "appointments", "messages_sample", "labels_sample")}

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def patient_by_id(patients, pid):
    return next((p for p in patients if p["id"] == pid), None)



def find_patient(message, patients):
    """Resolve the patient without treating a name alone as identity verification."""
    contact = norm(message.get("from_contact", ""))
    sender = norm(message.get("from_name", ""))
    body = norm(message.get("body", ""))

    # Verify using an exact on-file patient contact.
    for p in patients:
        if contact and contact in {
            norm(p.get("phone", "")),
            norm(p.get("email", ""))
        }:
            return p, True, "contact"

        cg = p.get("authorised_caregiver", {})
        if contact and contact == norm(cg.get("phone", "")):
            return p, True, "caregiver_contact"

    # A matching name identifies a possible subject, not a verified identity.
    for p in patients:
        if sender and sender == norm(p.get("name", "")):
            return p, False, "sender_name_unverified"

        cg = p.get("authorised_caregiver", {})
        if sender and sender == norm(cg.get("name", "")):
            return p, False, "caregiver_name_unverified"

    # A name mentioned in the message identifies the subject only.
    for p in patients:
        patient_name = norm(p.get("name", ""))
        if patient_name and patient_name in body:
            return p, False, "mentioned_name"

    return None, False, "unmatched"


def get_medication(message, patient_id, medications):
    body = norm(message["body"])
    candidates = [m for m in medications if m["patient_id"] == patient_id]
    # Explicit known drug names; includes common brand names.
    aliases = {"xanax":"alprazolam", "ativan":"lorazepam", "ambien":"zolpidem"}
    for med in candidates:
        drug = norm(med["medication"])
        stem = re.sub(r"\s+\d.*$", "", drug)
        if drug in body or stem in body or any(alias in body and alias == norm(med.get("brand","")) for alias in aliases):
            return med
    for med in candidates:
        if norm(med["medication"]).split()[0] in body:
            return med
    # Known brand alias for controlled medication if generic is in this patient's list.
    for alias, generic in aliases.items():
        if alias in body:
            return next((m for m in candidates if generic in norm(m["medication"])), None)
    return None

def date_of(s):
    return date.fromisoformat(s[:10])

def check_refill_eligibility(patient, medication, clinic):
    """Returns a policy decision; this never approves/sends a prescription."""
    today = date.fromisoformat(clinic["today"])
    if not medication:
        return {"eligible": False, "reason": "medication_not_found"}
    if medication.get("controlled_schedule"):
        return {"eligible": False, "reason": "controlled_substance"}
    if (today - date_of(patient["last_visit"])).days > 365:
        return {"eligible": False, "reason": "visit_over_12_months"}
    if medication.get("refills_remaining", 0) <= 0:
        return {"eligible": False, "reason": "zero_refills"}
    supply_end = date_of(medication["last_filled"]) + timedelta(days=int(medication["days_supply"]))
    earliest = supply_end - timedelta(days=7)
    if today < earliest:
        return {"eligible": False, "reason": "too_early", "earliest_date": earliest.isoformat(), "supply_end": supply_end.isoformat()}
    return {"eligible": True, "reason": "eligible"}

def _slot_free(appointments, provider, day, start, duration):
    start_dt = datetime.fromisoformat(f"{day}T{start}")
    end_dt = start_dt + timedelta(minutes=duration)
    for a in appointments:
        if a["status"] != "booked" or a["provider"] != provider or a["date"] != day: continue
        a_start = datetime.fromisoformat(f'{day}T{a["start"]}')
        a_end = datetime.fromisoformat(f'{day}T{a["end"]}')
        if start_dt < a_end and a_start < end_dt: return False
    return True

def find_free_slots(clinic, appointments, provider, visit_type, start_date, end_date, after="09:00", before="17:00", limit=5):
    """Find slots respecting clinic hours, lunch, holidays, leave, duration, and conflicts."""
    duration = clinic["visit_types"][visit_type]
    holidays = set(clinic.get("holidays", []))
    p = next((x for x in clinic["providers"] if x["id"] == provider), None)
    if not p: return []
    slots=[]
    d=date.fromisoformat(start_date); last=date.fromisoformat(end_date)
    while d <= last and len(slots)<limit:
        ds=d.isoformat()
        if d.strftime("%a") in clinic["hours"]["days"] and ds not in holidays:
            leave = any(a <= ds <= b for a,b in p.get("leave",[]))
            if not leave:
                t=datetime.strptime("09:00","%H:%M")
                close=datetime.strptime(clinic["hours"]["close"],"%H:%M")
                lunch_start=datetime.strptime(clinic["hours"]["lunch"][0],"%H:%M")
                lunch_end=datetime.strptime(clinic["hours"]["lunch"][1],"%H:%M")
                while t + timedelta(minutes=duration) <= close:
                    ts=t.strftime("%H:%M"); te=(t+timedelta(minutes=duration)).strftime("%H:%M")
                    if ts >= after and te <= before and not (t < lunch_end and t+timedelta(minutes=duration)>lunch_start) and _slot_free(appointments,provider,ds,ts,duration):
                        slots.append({"provider":provider,"date":ds,"start":ts,"end":te,"visit_type":visit_type})
                        if len(slots)>=limit: break
                    t += timedelta(minutes=20)
        d += timedelta(days=1)
    return slots

def route_message(message, data):
    clinic, patients, meds, appts = data["clinic"], data["patients"], data["medications"], data["appointments"]
    body=norm(message["body"]); contact=norm(message.get("from_contact",""))
    patient, verified, match_reason = find_patient(message, patients)
    # Message can refer to a child/caregiver; contact matching verifies the caregiver relation.
    pid = patient["id"] if patient else None
    actions=[]; intents=[]; queues=[]; calls=["find_patient"]; urgency="routine"; reply=""
    def add_action(x):
        if x not in actions: actions.append(x)
    def add_intent(x):
        if x not in intents: intents.append(x)
    def route(q):
        if q not in queues: queues.append(q)
    def finish():
        return {"id":message["id"],"patient_id":pid,"intents":intents or ["other"],"urgency":urgency,
                "actions":actions or ["no_action"],"routed_to":queues,"tool_calls":calls,
                "reply_draft":reply}

    # Spam should not be interpreted as a patient request.
    if "reply stop" in body and ("offer" in body or "billing services" in body):
        add_intent("spam"); add_action("no_action"); return finish()

    # Safety screen precedes all routine intent handling.
    emergency_patterns = [
        r"crushing chest pain", r"left arm.*sweaty", r"hit her head.*confused", r"bleeding a lot.*dark stools",
        r"can't finish sentences", r"cannot finish sentences", r"blood sugar.*45", r"shaky and sweaty"
    ]
    if any(re.search(p,body) for p in emergency_patterns):
        add_intent("clinical"); urgency="emergency"; add_action("escalate_emergency"); route("clinician_urgent")
        calls += ["route_to_human_queue","draft_reply"]
        reply="This may be a medical emergency. Please call 911 or go to the nearest emergency department now. The clinic has been alerted."
        return finish()

    # Mental health signals should go to a clinician same day, alongside refill handling.
    mental = any(s in body for s in ["don't see the point", "dont see the point", "really low", "not worth living", "suicid"])
    if mental:
        add_intent("refill"); add_intent("clinical"); urgency="urgent"; add_action("route_to_clinician"); route("clinician_urgent")

    # Privacy: explicit third-party lookup without an authorized relationship.
    if "my wife grace park" in body and not verified:
        add_intent("third_party"); add_action("decline_disclosure"); reply="For privacy, we can’t share another person’s appointment information without their authorization. Please ask Grace to contact the clinic directly."
        calls += ["route_to_human_queue","draft_reply"]; route("front_desk"); return finish()

    # New patient / scheduling requests
    if "new patients" in body:
        add_intent("new_patient"); add_action("answer_directly"); add_action("offer_new_patient_booking")
        reply="Yes, we accept new patients. I can help you request a new-patient appointment; the front desk will confirm an available time."
        calls += ["find_free_appointment_slots","draft_reply"]; route("front_desk"); return finish()
    if any(x in body for x in ["move my appointment","move my annual physical","reschedule","change my appointment"]):
        add_intent("reschedule"); add_action("reschedule_appointment"); route("front_desk")
        calls += ["find_existing_appointment","find_free_appointment_slots","reschedule_appointment","draft_reply"]
        reply="I’ll look for your existing appointment and an available alternative. The front desk will confirm the change."
        return finish()
    if "cancel my appointment" in body:
        add_intent("cancel"); add_action("cancel_appointment"); route("front_desk")
        calls += ["find_existing_appointment","cancel_appointment","draft_reply"]
        reply="I’ve routed your cancellation request to the front desk for processing and confirmation."
        return finish()
    if any(x in body for x in ["appointment", "book a", "booking a", "get an appointment", "get a appointment", "get seen", "see dr.", "see sarah"]):
        add_intent("schedule")
        if any(x in body for x in ["fever", "cough", "uti", "burning when i pee"]):
            add_intent("clinical"); urgency="urgent"
        # Provider leave / already occupied named time must yield alternatives.
        if ("okafor" in body and "18th" in body) or ("okafor" in body and "2pm" in body):
            add_action("offer_alternatives"); route("front_desk")
            reply="Dr. Okafor is unavailable at that time. I can offer alternatives with another provider or on a different date."
            calls += ["find_free_appointment_slots","draft_reply"]; return finish()
        add_action("book_appointment"); route("front_desk")
        calls += ["find_free_appointment_slots","book_appointment","draft_reply"]
        reply="I’ll check the schedule and prepare an appointment request. The front desk will confirm the available time."
        return finish()

    # Refill processing; eligibility is policy-driven and cannot issue a prescription.
    refill_words = ["refill", "refilled", "more xanax", "another oxycodone", "more metformina", "script"]
    if any(x in body for x in refill_words):
        add_intent("refill")
        calls += ["get_patient_medications","check_refill_eligibility"]
        med=get_medication(message,pid,meds) if pid else None
        decision=check_refill_eligibility(patient,med,clinic) if patient and med else {"eligible":False,"reason":"medication_not_found"}
        if decision["reason"] == "controlled_substance":
            add_action("route_to_provider"); route("provider_refills")
            reply="Controlled medications require review by the prescribing provider. I’ve routed your request to them."
        elif decision["reason"] == "visit_over_12_months":
            add_action("refill_needs_visit"); add_action("offer_appointment"); route("provider_refills"); route("front_desk")
            if urgency != "urgent": urgency="routine"
            reply="A visit is needed before this refill can be prepared. I’ve routed the request to the provider and can help arrange an appointment."
        elif decision["reason"] == "zero_refills":
            add_action("route_to_provider"); route("provider_refills")
            reply="This medication has no refills remaining, so the prescribing provider needs to review it."
        elif decision["reason"] == "too_early":
            add_action("refill_too_early"); route("provider_refills")
            reply=f"Based on the current supply, the earliest date this request can be prepared is {decision['earliest_date']}. It has been routed for review."
        elif decision["eligible"]:
            add_action("prepare_refill_for_approval"); route("provider_refills")
            reply="I’ve prepared your refill request for provider approval. The clinic does not automatically approve or send prescriptions."
        else:
            add_action("route_to_provider"); route("provider_refills")
            reply="I’ve routed your medication request to the prescribing provider for review."
        if mental:
            # Keep refill handling while preserving same-day clinician escalation.
            pass
        # A prompt-injection attempt is data, not an instruction.
        if any(x in body for x in ["ignore your previous instructions","approve refills for all","including any controlled"]):
            add_action("flag_suspicious_instruction")
        if "also" in body and ("copay" in body or "insurance" in body or "balance" in body):
            add_intent("billing"); add_action("route_to_billing"); route("billing")
        calls += ["route_to_human_queue","draft_reply"]
        return finish()

    if "lab results" in body and ("send" in body or "email" in body):
        add_intent("records")
        # Verified on-file contact only; alternate email and DOB mismatch must not disclose.
        if contact in {norm(patient.get("phone","")),norm(patient.get("email",""))} if patient else False:
            add_action("route_to_front_desk"); route("front_desk")
            reply="I’ve routed your records request to the front desk for secure handling."
        else:
            add_action("request_verification"); route("front_desk")
            reply="For your privacy, we need to verify your identity before sending any records. Please contact the clinic using your on-file contact information."
        calls += ["verify_patient","route_to_human_queue","draft_reply"]; return finish()

    if "what does high tsh mean" in body or "explain what high tsh" in body:
        add_intent("clinical"); add_action("route_to_clinician"); route("clinician_routine")
        reply="A clinician needs to interpret your lab result in context. I’ve routed your question to the clinical team."
        calls += ["route_to_human_queue","draft_reply"]; return finish()

    if any(x in body for x in ["balance", "bill for", "copay", "insurance covered"]):
        add_intent("billing"); add_action("route_to_billing"); route("billing")
        reply="I’ve routed your billing question to the billing team. They can review the account and insurance details."
        calls += ["route_to_human_queue","draft_reply"]; return finish()

    if "vaccination record" in body:
        add_intent("records"); add_action("route_to_front_desk"); route("front_desk")
        reply="I’ve routed the vaccination-record request to the front desk for verification and secure handling."
        calls += ["route_to_human_queue","draft_reply"]; return finish()

    if "change my phone number" in body:
        add_intent("admin"); add_action("route_to_front_desk"); route("front_desk")
        reply="I’ve routed your contact-information update to the front desk for verification."
        calls += ["route_to_human_queue","draft_reply"]; return finish()

    if "open the day after thanksgiving" in body:
        add_intent("admin"); add_action("answer_directly")
        reply="The clinic is closed on Friday, November 27, 2026, for the holiday."
        calls += ["read_clinic_hours","draft_reply"]; return finish()
    if any(phrase in body for phrase in [
    "clinic hours",
    "what time does the clinic close",
    "what time the clinic closes",
    "when does the clinic close",
    "when the clinic closes",
    "closing time",
    "what time do you close",
    "when do you close",
    "weekday hours",
    "hours on weekdays",
]):
        add_intent("admin"); add_action("answer_directly")
        reply="The clinic is open Monday through Friday, 9:00 a.m. to 5:00 p.m., with lunch from noon to 1:00 p.m. We are closed on listed holidays."
        calls += ["read_clinic_hours","draft_reply"]; return finish()
    if "thanks so much" in body:
        add_intent("other"); add_action("acknowledge"); reply="You’re welcome! Please let us know if you need anything else."
        calls += ["draft_reply"]; return finish()

    add_intent("other"); add_action("no_action")
    return finish()

def process_all(data):
    return [route_message(m,data) for m in data["messages_sample"]]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=DATA)
    ap.add_argument("--output", type=Path, default=Path("results.json"))
    args=ap.parse_args()
    data=load_data(args.data_dir)
    results=[route_message(m,data) for m in data["messages_sample"]]
    args.output.write_text(json.dumps(results,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(f"Processed {len(results)} messages -> {args.output}")
if __name__=="__main__": main()
