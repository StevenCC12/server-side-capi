import logging
import sys
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
if os.getenv("RENDER") is None:
    from dotenv import load_dotenv
    load_dotenv()

# --- ROBUST LOGGING CONFIGURATION ---
logger = logging.getLogger()
logger.setLevel(logging.INFO)

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(handler)

# --- Configuration ---
FB_PIXEL_ID = os.getenv("FB_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")

CAPI_URL = f"https://graph.facebook.com/v24.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"
FBP_REGEX = re.compile(r'^fb\.1\.\d+\.[a-zA-Z0-9]+$') 

app = FastAPI()

origins = [
    "https://summit.carlhelgesson.com",
    "http://localhost:8000",
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
    event_id: Optional[str] = None
    event_name: str
    event_time: int
    event_source_url: Optional[str] = None
    action_source: str
    user_data: dict 
    custom_data: Optional[dict] = None

class MetaTestPayload(BaseModel):
    data: List[ClientPayload]
    test_event_code: Optional[str] = None

def hash_data(value: Optional[str], format_type: str = "default") -> str:
    """Normalizes and hashes data according to Meta's strict specifications."""
    if not value: return ""
    
    val_str = str(value).strip().lower()
    
    # Meta-specific normalization rules
    if format_type == "phone":
        # Remove symbols, letters, and any leading zeros
        val_str = re.sub(r'\D', '', val_str).lstrip('0')
    elif format_type == "city":
        # No punctuation, no special characters, no spaces
        val_str = re.sub(r'[\W_]+', '', val_str)
    elif format_type == "zip":
        # No spaces, no dashes
        val_str = val_str.replace(" ", "").replace("-", "")
    elif format_type == "dob":
        # Strip all punctuation to force YYYYMMDD if formatting included dashes/slashes
        val_str = re.sub(r'\D', '', val_str)
        
    if not val_str: return ""
    return hashlib.sha256(val_str.encode()).hexdigest()

# --- Core Processor ---
async def _process_single_event(payload: ClientPayload, request: Request, test_event_code: Optional[str] = None):
    # 1. Identity & Network Data
    fbc_val = payload.user_data.get("fbc")
    fbp_val = payload.user_data.get("fbp")

    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else (request.client.host if request.client else "")
    
    client_user_agent = payload.user_data.get("user_agent") or request.headers.get("user-agent")

    # 2. Clean Custom Data
    final_custom_data = {k: v for k, v in (payload.custom_data or {}).items() if v not in [None, "null", "NULL"]}
    
    if "value" in final_custom_data:
        try:
            val = float(final_custom_data["value"])
            if val >= 0:
                final_custom_data["value"] = val
            else:
                final_custom_data.pop("value", None)
                final_custom_data.pop("currency", None)
        except:
            final_custom_data.pop("value", None)
            final_custom_data.pop("currency", None)

    if "value" in final_custom_data and "currency" not in final_custom_data:
        final_custom_data["currency"] = "SEK"

    # 3. Hash PII (Updated to include full EMQ parameter mapping)
    meta_user_data = {
        "client_ip_address": client_ip,
        "client_user_agent": client_user_agent,
        "fbc": fbc_val if fbc_val and fbc_val.lower() != "null" else None,
        "fbp": fbp_val if fbp_val and FBP_REGEX.match(fbp_val) else None,
        "em": hash_data(payload.user_data.get("email")),
        "fn": hash_data(payload.user_data.get("first_name")),
        "ln": hash_data(payload.user_data.get("last_name")),
        "ph": hash_data(payload.user_data.get("phone"), format_type="phone"),
        "ct": hash_data(payload.user_data.get("city"), format_type="city"),
        "st": hash_data(payload.user_data.get("state")),
        "zp": hash_data(payload.user_data.get("zip"), format_type="zip"),
        "country": hash_data(payload.user_data.get("country")),
        "external_id": hash_data(payload.user_data.get("external_id")),
        "db": hash_data(payload.user_data.get("db") or payload.user_data.get("date_of_birth"), format_type="dob"),
        "ge": hash_data(payload.user_data.get("ge") or payload.user_data.get("gender"))
    }
    
    # Strip out any keys where the value is empty/None
    meta_user_data = {k: v for k, v in meta_user_data.items() if v}

    # 4. Construct Payload
    event_data = {
        "event_name": payload.event_name,
        "event_time": payload.event_time,
        "action_source": payload.action_source,
        "user_data": meta_user_data,
        "custom_data": final_custom_data,
        "event_source_url": payload.event_source_url,
        "event_id": payload.event_id
    }

    final_payload: Dict[str, Any] = {"data": [event_data]}
    
    # UPDATE 1: Only attach test code if explicitly passed in the JSON webhook payload
    if test_event_code:
        final_payload["test_event_code"] = test_event_code
        logging.info(f"🚀 SENDING TEST EVENT TO META: Event ID {payload.event_id} | Test Code: {test_event_code}")
    else:
        logging.info(f"🚀 SENDING LIVE EVENT TO META: Event ID {payload.event_id} | No Test Code")

    # 5. Send to Meta
    try:
        resp = requests.post(CAPI_URL, json=final_payload)
        resp.raise_for_status()
        
        logging.info(f"✅ META SUCCESS: {resp.json()}")
        return resp.json()
        
    # UPDATE 2: Catch requests exceptions specifically to extract Meta's error body
    except requests.exceptions.RequestException as e:
        error_details = str(e)
        if e.response is not None:
            try:
                # Try to parse Meta's JSON error response
                meta_error = e.response.json()
                error_details = json.dumps(meta_error)
            except ValueError:
                # Fallback to plain text if it's not JSON
                error_details = e.response.text
                
        logging.error(f"❌ META API REJECTED PAYLOAD: {error_details}")
        raise HTTPException(status_code=400, detail=f"Meta API Error: {error_details}")

# --- Endpoints ---
@app.post("/process-event")
async def process_event(request: Request):
    print("👀 RECEIVED WEBHOOK REQUEST!", flush=True) 
    
    try:
        raw_body = await request.json()
        print(f"📦 PAYLOAD: {json.dumps(raw_body)}", flush=True) 
    except Exception:
        print("❌ FAILED TO READ JSON", flush=True)
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Detect Batch vs Single
    if "data" in raw_body and isinstance(raw_body["data"], list):
        try:
            payload_obj = MetaTestPayload(**raw_body)
            events = payload_obj.data
            t_code = payload_obj.test_event_code 
        except ValidationError as e:
            print(f"❌ VALIDATION ERROR (Batch): {str(e)}", flush=True)
            raise HTTPException(status_code=422, detail=str(e))
    else:
        try:
            payload_obj = ClientPayload(**raw_body)
            events = [payload_obj]
            # Since it's a single event structure without the outer wrapper, there is no test code
            t_code = None 
        except ValidationError as e:
            print(f"❌ VALIDATION ERROR (Single): {str(e)}", flush=True)
            raise HTTPException(status_code=422, detail=str(e))

    results = []
    for event in events:
        try:
            res = await _process_single_event(event, request, t_code)
            results.append({"status": "success", "response": res})
        except HTTPException as e:
            # We already logged the detailed error in _process_single_event
            results.append({"status": "error", "message": e.detail})
        except Exception as e:
            print(f"❌ PROCESS ERROR: {str(e)}", flush=True)
            results.append({"status": "error", "message": str(e)})

    return {"results": results}

@app.get("/health-check")
def health_check():
    print("💓 Health check heartbeat", flush=True)
    return {"status": "ok", "mode": "Live"}