# ===================================================================
#  TEST SCRIPT: purchase_capi_test.py (v2 - Final Test Version)
# ===================================================================
import logging
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import hashlib
import os
import re
import json
import ipaddress
from typing import Optional

# --- Standard Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
FBP_REGEX = re.compile(r'^fb\.1\.\d+\.\d+$')
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://summit.carlhelgesson.com", "https://masterclass.carlhelgesson.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NEW: This is our "Digital Coat Check" in-memory cache ---
event_id_cache = {}

class ClientPayload(BaseModel):
    event_id: Optional[str] = None
    event_name: str
    event_time: int
    event_source_url: Optional[str] = None
    action_source: str
    user_data: dict
    custom_data: Optional[dict] = None

def hash_data(value: str) -> str:
    if not value: return ""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

def sanitize_phone_number(phone_str: str) -> str:
    if not phone_str: return ""
    digits_only = re.sub(r'\D', '', phone_str)
    if digits_only.startswith('0'):
        return '46' + digits_only[1:]
    return digits_only

# --- NEW: Health check endpoint for easy debugging ---
@app.get("/health-check")
def health_check():
    return {"status": "ok", "message": "The new TEST code is live!"}

# --- NEW: The endpoint for our "Coat Check" ---
@app.post("/cache-event-id")
async def cache_event_id(payload: dict = Body(...)):
    email = payload.get("email")
    event_id = payload.get("event_id")
    if email and event_id:
        event_id_cache[email.strip().lower()] = event_id
        logging.info(f"Cached event_id {event_id} for email {email}")
        return {"status": "cached"}
    raise HTTPException(status_code=400, detail="Missing email or event_id")

@app.post("/process-event")
def process_event(payload: ClientPayload, request: Request):
    payload_dict = payload.model_dump()
    
    # --- UPDATED: Event Deduplication Logic ---
    event_id = payload_dict.get("event_id")
    if not event_id and payload.event_name == "Purchase":
        email = payload.user_data.get("email")
        if email:
            normalized_email = email.strip().lower()
            if normalized_email in event_id_cache:
                event_id = event_id_cache.pop(normalized_email)
                logging.info(f"Found and attached cached event_id {event_id} for email {email}")
            else:
                logging.warning(f"No cached event_id found for email {email}")
        else:
            logging.warning("Purchase event from webhook had no email to look up event_id.")
            
    logging.info(f"Received event payload: {payload_dict}")

    # --- Data Extraction and Validation ---
    fbc_val = payload.user_data.get("fbc", "") or ""
    fbp_val = payload.user_data.get("fbp", "") or ""
    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = x_forwarded_for.split(',')[0].strip() if x_forwarded_for else request.client.host
    client_user_agent = payload.user_data.get("user_agent") or request.headers.get("user-agent", "")

    # --- Hashing PII ---
    hashed_email = hash_data(payload.user_data.get("email", ""))
    hashed_first_name = hash_data(payload.user_data.get("first_name", ""))
    hashed_last_name = hash_data(payload.user_data.get("last_name", ""))
    hashed_country = hash_data(payload.user_data.get("country", "").lower())
    hashed_city = hash_data(payload.user_data.get("city", ""))
    hashed_zip = hash_data(payload.user_data.get("zip", ""))
    sanitized_phone = sanitize_phone_number(payload.user_data.get("phone", ""))
    hashed_phone = hash_data(sanitized_phone)
    hashed_external_id = hash_data(payload.user_data.get("external_id", ""))

    # --- Building the Meta Payload ---
    meta_payload_user_data = {
        "client_ip_address": client_ip,
        "client_user_agent": client_user_agent,
    }
    if hashed_email: meta_payload_user_data["em"] = hashed_email
    if hashed_first_name: meta_payload_user_data["fn"] = hashed_first_name
    if hashed_last_name: meta_payload_user_data["ln"] = hashed_last_name
    if hashed_phone: meta_payload_user_data["ph"] = hashed_phone
    if hashed_country: meta_payload_user_data["country"] = hashed_country
    if hashed_city: meta_payload_user_data["ct"] = hashed_city
    if hashed_zip: meta_payload_user_data["zp"] = hashed_zip
    if hashed_external_id: meta_payload_user_data["external_id"] = hashed_external_id
    if fbc_val: meta_payload_user_data["fbc"] = fbc_val
    if fbp_val and FBP_REGEX.match(fbp_val): meta_payload_user_data["fbp"] = fbp_val

    meta_payload_event_data = {
        "event_name": payload.event_name,
        "event_time": payload.event_time,
        "action_source": "website",
        "user_data": {k: v for k, v in meta_payload_user_data.items() if v}, # Clean None/empty values
        "custom_data": payload.custom_data,
        "event_source_url": payload.event_source_url,
    }
    
    if event_id:
        meta_payload_event_data["event_id"] = event_id

    meta_payload = {"data": [meta_payload_event_data]}
    logging.info("Built Meta CAPI payload: %s", json.dumps(meta_payload, indent=2))
    
    # --- TEST MODE: Log instead of sending ---
    logging.info("Event would be successfully sent to Meta under the exact same conditions it would be sent")
    return {
        "meta_response": {
            "status_code": 200,
            "response": {"message": "Test mode: Event not sent to Meta."}
        }
    }