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
import time
from datetime import datetime, timezone, timedelta
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

# --- GHL / Purchase Webhook Config ---
GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")
GHL_API_BASE = "https://services.leadconnectorhq.com"

PURCHASE_DEFAULT_VALUE = 21997.00
PURCHASE_DOWNSELL_VALUE = 4997.00
PURCHASE_DEFAULT_URL = "https://checkout.knaa.se/order-page"
PURCHASE_DOWNSELL_URL = "https://checkout.knaa.se/ds"

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

# --- GHL Helpers ---

def _ghl_headers() -> dict:
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "2021-07-28",
        "Accept": "application/json",
    }

def _lookup_ghl_contact(email: str = "", phone: str = "") -> Optional[dict]:
    """Search GHL by email/phone, then fetch the full contact for attributionSource."""
    if not GHL_API_KEY:
        logging.warning("GHL_API_KEY not set — skipping CRM lookup")
        return None

    params: Dict[str, str] = {}
    if GHL_LOCATION_ID:
        params["locationId"] = GHL_LOCATION_ID
    if email:
        params["email"] = email.strip().lower()
    elif phone:
        params["phone"] = phone
    else:
        return None

    try:
        resp = requests.get(f"{GHL_API_BASE}/contacts/search/duplicate", headers=_ghl_headers(), params=params, timeout=15)
        resp.raise_for_status()
        contact = resp.json().get("contact")
    except Exception as e:
        logging.error(f"GHL search failed: {e}")
        return None

    if not contact:
        logging.warning(f"No GHL contact found for: {email or phone}")
        return None

    contact_id = contact.get("id")
    if not contact_id:
        return contact

    try:
        resp = requests.get(f"{GHL_API_BASE}/contacts/{contact_id}", headers=_ghl_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json().get("contact", contact)
    except Exception as e:
        logging.error(f"GHL contact fetch failed (falling back to search result): {e}")
        return contact


def _normalize_phone_e164(phone: str) -> str:
    """Swedish local number → E.164 (+46...)."""
    if not phone:
        return ""
    cleaned = re.sub(r"[\s\-\.\(\)]+", "", phone.strip())
    if cleaned.startswith("0"):
        cleaned = "+46" + cleaned[1:]
    elif not cleaned.startswith("+") and cleaned.startswith("46"):
        cleaned = "+" + cleaned
    return cleaned


def _resolve_fbc(attribution: dict) -> str:
    """Return fbc if present, otherwise construct from fbclid."""
    fbc = attribution.get("fbc")
    if fbc:
        return fbc
    fbclid = attribution.get("fbclid")
    if fbclid:
        return f"fb.1.{int(time.time() * 1000)}.{fbclid}"
    return ""


ATTRIBUTION_WINDOW_DAYS = 30

def _is_within_attribution_window(raw_webhook: dict, ghl_contact: dict) -> bool:
    """Check if the contact was created within the 30-day attribution window."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ATTRIBUTION_WINDOW_DAYS)

    # Try webhook date_created (unix timestamp or ISO string)
    date_val = raw_webhook.get("date_created") or ghl_contact.get("dateAdded") or ghl_contact.get("dateCreated")
    if not date_val:
        logging.warning("No creation date found — allowing event (cannot verify window)")
        return True

    try:
        if isinstance(date_val, (int, float)):
            created = datetime.fromtimestamp(date_val, tz=timezone.utc)
        else:
            cleaned = str(date_val).replace("Z", "+00:00")
            created = datetime.fromisoformat(cleaned)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OSError) as e:
        logging.warning(f"Could not parse creation date '{date_val}': {e} — allowing event")
        return True

    if created >= cutoff:
        days_ago = (now - created).days
        logging.info(f"✅ Contact created {days_ago} days ago — within {ATTRIBUTION_WINDOW_DAYS}-day window")
        return True
    else:
        days_ago = (now - created).days
        logging.info(f"⏭️ Contact created {days_ago} days ago — outside {ATTRIBUTION_WINDOW_DAYS}-day window, skipping")
        return False


# --- Core Processor ---
async def _process_single_event(payload: ClientPayload, request: Request, test_event_code: Optional[str] = None, client_ip_override: str = ""):
    # 1. Identity & Network Data
    fbc_val = payload.user_data.get("fbc")
    fbp_val = payload.user_data.get("fbp")

    if client_ip_override:
        client_ip = client_ip_override
    else:
        x_forwarded_for = request.headers.get("x-forwarded-for", "")
        client_ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else (request.client.host if request.client else "")
    
    client_user_agent = payload.user_data.get("user_agent") or request.headers.get("user-agent")

    # 2. Clean Custom Data
    final_custom_data = {k: v for k, v in (payload.custom_data or {}).items() if v not in [None, "null", "NULL"]}
    
    if "value" in final_custom_data:
        try:
            val = float(final_custom_data["value"])
            if val > 0: # Now strictly greater than 0
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

@app.post("/webhook/purchase")
async def webhook_purchase(request: Request):
    """GHL webhook → CRM lookup for attribution → Purchase event to Meta."""
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logging.info(f"📥 PURCHASE WEBHOOK: {json.dumps(raw, default=str)[:500]}")

    email = (raw.get("email") or "").strip().lower()
    phone = raw.get("phone") or ""
    if not email and not phone:
        raise HTTPException(status_code=400, detail="email or phone is required")

    contact = _lookup_ghl_contact(email=email, phone=phone)
    if not contact:
        raise HTTPException(status_code=404, detail=f"Contact not found in CRM for: {email or phone}")

    if not _is_within_attribution_window(raw, contact):
        return {"status": "skipped", "reason": f"Contact older than {ATTRIBUTION_WINDOW_DAYS} days"}

    attribution = contact.get("attributionSource") or {}
    external_id = contact.get("id", "")
    client_ip = attribution.get("ip", "")

    # Detect purchase type from contact tags
    contact_tags = [t.lower() if isinstance(t, str) else "" for t in (contact.get("tags") or [])]

    if any("special deal" in tag for tag in contact_tags):
        logging.info(f"⏭️ Special deal tag found for {email} — skipping")
        return {"status": "skipped", "reason": "Special deal — not PIF or downsell"}

    downsell = any("downsell" in tag for tag in contact_tags)

    value = PURCHASE_DOWNSELL_VALUE if downsell else PURCHASE_DEFAULT_VALUE
    event_source_url = PURCHASE_DOWNSELL_URL if downsell else PURCHASE_DEFAULT_URL
    logging.info(f"{'📦 DOWNSELL' if downsell else '💰 PIF'} detected for {email} — {value} SEK")

    custom_data: Dict[str, Any] = {"currency": "SEK", "value": value}
    seg = raw.get("customer_segmentation")
    if seg:
        custom_data["customer_segmentation"] = seg

    payload = ClientPayload(
        event_name="Purchase",
        event_time=int(time.time()),
        event_id=f"{external_id}-Purchase",
        action_source="website",
        event_source_url=event_source_url,
        user_data={
            "email": contact.get("email") or email,
            "first_name": contact.get("firstName") or raw.get("first_name", ""),
            "last_name": contact.get("lastName") or raw.get("last_name", ""),
            "phone": _normalize_phone_e164(contact.get("phone") or phone),
            "external_id": external_id,
            "city": contact.get("city") or raw.get("city", ""),
            "zip": (contact.get("postalCode") or raw.get("postal_code") or "").replace(" ", ""),
            "country": (contact.get("country") or raw.get("country") or "se").lower(),
            "fbc": _resolve_fbc(attribution),
            "fbp": attribution.get("fbp") or "",
            "user_agent": attribution.get("userAgent") or "",
        },
        custom_data=custom_data,
    )

    logging.info(f"🔄 PROCESSING PURCHASE: {external_id} | {payload.user_data['email']} | {value} SEK")

    try:
        result = await _process_single_event(payload, request, client_ip_override=client_ip)
        return {"status": "success", "event_id": payload.event_id, "response": result}
    except HTTPException as e:
        logging.error(f"❌ PURCHASE FAILED: {e.detail}")
        return {"status": "error", "event_id": payload.event_id, "message": e.detail}


@app.get("/health-check")
def health_check():
    print("💓 Health check heartbeat", flush=True)
    return {"status": "ok", "mode": "Live"}