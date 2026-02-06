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
# This setup ensures logs are visible in the Render console immediately.
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Create a handler for Standard Output
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
ENV_TEST_CODE = os.getenv("META_TEST_CODE") 

CAPI_URL = f"https://graph.facebook.com/v24.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"
FBP_REGEX = re.compile(r'^fb\.1\.\d+\.[a-zA-Z0-9]+$') 

app = FastAPI()

# Add your live domain here
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

def hash_data(value: Optional[str]) -> str:
    if not value: return ""
    return hashlib.sha256(str(value).strip().lower().encode()).hexdigest()

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
        try: final_custom_data["value"] = float(final_custom_data["value"])
        except: final_custom_data["value"] = 0.0
    if "currency" not in final_custom_data: final_custom_data["currency"] = "SEK"

    # 3. Hash PII
    meta_user_data = {
        "client_ip_address": client_ip,
        "client_user_agent": client_user_agent,
        "fbc": fbc_val if fbc_val and fbc_val.lower() != "null" else None,
        "fbp": fbp_val if fbp_val and FBP_REGEX.match(fbp_val) else None,
        "em": hash_data(payload.user_data.get("email")),
        "fn": hash_data(payload.user_data.get("first_name")),
        "ln": hash_data(payload.user_data.get("last_name")),
        "ph": hash_data(payload.user_data.get("phone")),
    }
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
    
    # Priority: Code from JSON > Code from Environment
    actual_test_code = test_event_code or ENV_TEST_CODE
    if actual_test_code:
        final_payload["test_event_code"] = actual_test_code

    # 5. Send to Meta
    try:
        # We keep logging info here for consistency
        logging.info(f"🚀 SENDING TO META: Event ID {payload.event_id} | Test Code: {actual_test_code}")
        
        resp = requests.post(CAPI_URL, json=final_payload)
        resp.raise_for_status()
        
        logging.info(f"✅ META SUCCESS: {resp.json()}")
        return resp.json()
    except Exception as e:
        logging.error(f"❌ META ERROR: {str(e)}")
        raise e

# --- Endpoints ---

@app.post("/process-event")
async def process_event(request: Request):
    # --- BRUTE FORCE DEBUGGING START ---
    print("👀 RECEIVED WEBHOOK REQUEST!", flush=True) 
    
    try:
        raw_body = await request.json()
        print(f"📦 PAYLOAD: {json.dumps(raw_body)}", flush=True) 
    except Exception:
        print("❌ FAILED TO READ JSON", flush=True)
        raise HTTPException(status_code=400, detail="Invalid JSON")
    # --- BRUTE FORCE DEBUGGING END ---

    # Detect Batch vs Single
    if "data" in raw_body and isinstance(raw_body["data"], list):
        try:
            payload_obj = MetaTestPayload(**raw_body)
            events = payload_obj.data
            t_code = payload_obj.test_event_code 
        except ValidationError as e:
            # Print specific validation errors too!
            print(f"❌ VALIDATION ERROR (Batch): {str(e)}", flush=True)
            raise HTTPException(status_code=422, detail=str(e))
    else:
        try:
            payload_obj = ClientPayload(**raw_body)
            events = [payload_obj]
            t_code = None 
        except ValidationError as e:
            # Print specific validation errors too!
            print(f"❌ VALIDATION ERROR (Single): {str(e)}", flush=True)
            raise HTTPException(status_code=422, detail=str(e))

    results = []
    for event in events:
        try:
            res = await _process_single_event(event, request, t_code)
            results.append({"status": "success", "response": res})
        except Exception as e:
            print(f"❌ PROCESS ERROR: {str(e)}", flush=True)
            results.append({"status": "error", "message": str(e)})

    return {"results": results}

@app.get("/health-check")
def health_check():
    print("💓 Health check heartbeat", flush=True)
    return {"status": "ok", "mode": "Test" if ENV_TEST_CODE else "Live"}