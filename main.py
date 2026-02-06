import logging
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
import requests
import hashlib
import os
import re
import json
import ipaddress
from typing import Optional, List, Dict, Any

# --- Environment Setup ---
# Render sets RENDER=true. If missing, we load from local .env
if os.getenv("RENDER") is None:
    from dotenv import load_dotenv
    load_dotenv()

# --- Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

FB_PIXEL_ID = os.getenv("FB_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")

# If this is set in .env or Render, all events go to the "Test Events" tab.
# If this is None/Empty (in Production), events go to the real dataset.
ENV_TEST_CODE = os.getenv("META_TEST_CODE") 

# Check for critical vars
if not FB_PIXEL_ID or not FB_ACCESS_TOKEN:
    logging.warning("⚠️  Missing FB_PIXEL_ID or FB_ACCESS_TOKEN. CAPI requests will fail.")

# API Version
CAPI_URL = f"https://graph.facebook.com/v24.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"

# Updated to allow letters in the final segment, just in case
FBP_REGEX = re.compile(r'^fb\.1\.\d+\.[a-zA-Z0-9]+$')

app = FastAPI()

# --- CORS Configuration ---
origins = [
    "https://summit.carlhelgesson.com",
    "http://localhost:8000", # Good to keep for local testing
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Data Models ---

class ClientPayload(BaseModel):
    event_id: Optional[str] = None # Critical for Deduplication
    event_name: str
    event_time: int
    event_source_url: Optional[str] = None
    action_source: str
    user_data: dict 
    custom_data: Optional[dict] = None

class MetaTestPayload(BaseModel):
    data: List[ClientPayload]
    test_event_code: Optional[str] = None

# --- Helper Functions ---

def hash_data(value: Optional[str]) -> str:
    if not value:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

# --- Core Processor ---

async def _process_single_event(payload: ClientPayload, request: Request, test_event_code: Optional[str] = None):
    """
    Processes a single event: extracts IP/UA, hashes PII, and sends to Meta.
    """
    
    # 1. Extract Identity & Network Data
    fbc_val = payload.user_data.get("fbc")
    fbp_val = payload.user_data.get("fbp")

    # IP Handling: Priority to Payload, then Header, then Request Host
    payload_ip = payload.user_data.get("client_ip_address")
    if not payload_ip:
        x_forwarded_for = request.headers.get("x-forwarded-for", "")
        payload_ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else (request.client.host if request.client else "")
    
    # Validate IP
    try:
        if payload_ip: ipaddress.ip_address(payload_ip)
    except ValueError:
        payload_ip = None

    # User Agent Priority
    client_user_agent = payload.user_data.get("user_agent") or \
                        (payload.custom_data.get("user_agent_captured_client_side") if payload.custom_data else None) or \
                        request.headers.get("user-agent")

    # 2. Clean Custom Data
    final_custom_data = {k: v for k, v in (payload.custom_data or {}).items() if v not in [None, "null", "NULL"]}
    
    # Standardize Value/Currency
    if "value" in final_custom_data:
        try:
            final_custom_data["value"] = float(final_custom_data["value"])
        except (ValueError, TypeError):
            final_custom_data["value"] = 0.0
    
    if "currency" not in final_custom_data:
        final_custom_data["currency"] = "SEK" # Default currency

    # 3. Hash PII (Securely)
    # Note: We use lists [] for these fields because Meta sometimes prefers arrays for matching, 
    # though single strings are often accepted. Keeping it simple (string) works for most CAPI versions.
    meta_user_data = {
        "client_ip_address": payload_ip,
        "client_user_agent": client_user_agent,
        "fbc": fbc_val if fbc_val and fbc_val.lower() != "null" else None,
        "fbp": fbp_val if fbp_val and FBP_REGEX.match(fbp_val) else None,
        "em": hash_data(payload.user_data.get("email")),
        "fn": hash_data(payload.user_data.get("first_name")),
        "ln": hash_data(payload.user_data.get("last_name")),
        "ph": hash_data(payload.user_data.get("phone")),
        "ct": hash_data(payload.user_data.get("city")),
        "zp": hash_data(payload.user_data.get("zip")),
        "country": hash_data(payload.user_data.get("country")),
        "external_id": hash_data(payload.user_data.get("external_id")) if payload.user_data.get("external_id") else None
    }
    
    # Clean empty values
    meta_user_data = {k: v for k, v in meta_user_data.items() if v}

    # 4. Construct Final Meta Payload
    event_data = {
        "event_name": payload.event_name,
        "event_time": payload.event_time,
        "action_source": payload.action_source,
        "user_data": meta_user_data,
        "custom_data": final_custom_data,
    }

    if payload.event_source_url:
        event_data["event_source_url"] = payload.event_source_url
    
    # *** DEDUPLICATION KEY ***
    # This is the bridge. If the JS sends an ID, we pass it to Meta.
    if payload.event_id:
        event_data["event_id"] = payload.event_id

    # 5. Assemble Request Wrapper
    final_payload: Dict[str, Any] = {"data": [event_data]}

    # *** TEST CODE LOGIC ***
    # Priority: 1. Code sent in JSON (Postman) -> 2. Code set in Environment (.env/Render)
    actual_test_code = test_event_code or ENV_TEST_CODE
    
    if actual_test_code:
        final_payload["test_event_code"] = actual_test_code
        logging.info(f"🧪 Sending TEST Event. Code: {actual_test_code}")

    # 6. Dispatch
    try:
        resp = requests.post(CAPI_URL, json=final_payload)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Meta CAPI Error: {e}")
        if hasattr(e, 'response') and e.response is not None:
             logging.error(f"Meta Response: {e.response.text}")
        raise e

# --- Endpoints ---

@app.get("/health-check")
def health_check():
    return {
        "status": "ok", 
        "mode": "Test" if ENV_TEST_CODE else "Live",
        "service": "Webinar Lead CAPI"
    }

@app.post("/process-event")
async def process_event(request: Request):
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Detect Batch vs Single
    if "data" in raw_body and isinstance(raw_body["data"], list):
        try:
            payload_obj = MetaTestPayload(**raw_body)
            events = payload_obj.data
            # Code from JSON payload (e.g. Postman)
            t_code = payload_obj.test_event_code 
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        try:
            payload_obj = ClientPayload(**raw_body)
            events = [payload_obj]
            t_code = None 
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

    results = []
    for event in events:
        try:
            res = await _process_single_event(event, request, t_code)
            results.append({"status": "sent_to_meta", "response": res})
        except Exception as e:
            results.append({"status": "error", "message": str(e)})

    return {"results": results}