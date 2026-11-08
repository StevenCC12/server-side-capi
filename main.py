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
GHL_API_KEY_MARIA = os.getenv("GHL_API_KEY_MARIA")
GHL_LOCATION_ID_MARIA = os.getenv("GHL_LOCATION_ID_MARIA")
GHL_API_BASE = "https://services.leadconnectorhq.com"

MONTHLY_ITERATION_SHEET_ID = "1iJKkTG6Svo0r3kENdZDONXSs1nDDwDCJogLbFPDVyj8"

SWEDISH_DAYS = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
SWEDISH_MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
ENGLISH_MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

GHL_LOCATIONS = [
    {"name": "carl", "location_id": GHL_LOCATION_ID, "api_key": GHL_API_KEY},
    {"name": "maria", "location_id": GHL_LOCATION_ID_MARIA, "api_key": GHL_API_KEY_MARIA},
]

ROLLOVER_TAGS_TO_REMOVE = ["currently registered", "dragning"]

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


# --- Contact Data Validation ---

NAME_PARTICLES = {"von", "van", "de", "du", "af", "av", "der", "den", "di", "el", "la", "le"}


def _capitalize_name_part(part: str, is_first_word: bool) -> str:
    lower = part.lower()
    if not lower:
        return part
    if lower in NAME_PARTICLES and not is_first_word:
        return lower
    if "'" in lower and len(lower) > 2:
        idx = lower.index("'")
        return lower[:idx].capitalize() + "'" + lower[idx + 1:].capitalize()
    if lower.startswith("mc") and len(lower) > 3:
        return "Mc" + lower[2:].capitalize()
    return part.capitalize()


def _capitalize_name(name: str) -> str:
    if not name or not name.strip():
        return name or ""
    words = name.strip().split()
    result = []
    for i, word in enumerate(words):
        subparts = word.split("-")
        capitalized = "-".join(
            _capitalize_name_part(sub, is_first_word=(i == 0 and j == 0))
            for j, sub in enumerate(subparts)
        )
        result.append(capitalized)
    return " ".join(result)


def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + (0 if c1 == c2 else 1)))
        prev = curr
    return prev[-1]


DOMAIN_TYPOS: Dict[str, str] = {
    "gmai.com": "gmail.com", "gmial.com": "gmail.com", "gmal.com": "gmail.com",
    "gnail.com": "gmail.com", "gmil.com": "gmail.com", "gmsil.com": "gmail.com",
    "gmali.com": "gmail.com", "gmaol.com": "gmail.com", "gamil.com": "gmail.com",
    "gmaill.com": "gmail.com", "fmail.com": "gmail.com", "gmaik.com": "gmail.com",
    "gmaul.com": "gmail.com", "gimail.com": "gmail.com", "gemail.com": "gmail.com",
    "gail.com": "gmail.com", "gmaiil.com": "gmail.com",
    "gmail.co": "gmail.com", "gmail.vom": "gmail.com", "gmail.con": "gmail.com",
    "gmail.comn": "gmail.com",
    "hotmai.com": "hotmail.com", "hotmal.com": "hotmail.com", "hotmial.com": "hotmail.com",
    "hotmil.com": "hotmail.com", "hotmaill.com": "hotmail.com", "htmail.com": "hotmail.com",
    "homail.com": "hotmail.com", "hotmaul.com": "hotmail.com", "hotamil.com": "hotmail.com",
    "hotmail.con": "hotmail.com", "hotmail.vom": "hotmail.com", "hotmail.co": "hotmail.com",
    "hotmai.se": "hotmail.se", "hotmal.se": "hotmail.se",
    "outlok.com": "outlook.com", "outloo.com": "outlook.com", "outllook.com": "outlook.com",
    "outlokk.com": "outlook.com", "outloook.com": "outlook.com",
    "outlook.con": "outlook.com", "outlook.vom": "outlook.com",
    "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com", "yhoo.com": "yahoo.com",
    "yaoo.com": "yahoo.com", "yahho.com": "yahoo.com",
    "yahoo.con": "yahoo.com", "yahoo.vom": "yahoo.com",
    "iclould.com": "icloud.com", "iclod.com": "icloud.com", "icoud.com": "icloud.com",
    "icloud.con": "icloud.com",
    "live.con": "live.com", "live.vom": "live.com",
}

KNOWN_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com",
    "live.com", "live.se", "hotmail.se", "outlook.se", "yahoo.se",
    "msn.com", "aol.com", "protonmail.com", "proton.me", "me.com",
    "mail.com", "zoho.com", "telia.com", "bredband.net", "comhem.se",
    "spray.se", "pm.me", "mac.com", "ymail.com", "gmx.com", "gmx.de",
    "web.de", "fastmail.com", "tutanota.com", "tuta.com",
}

FUZZY_DOMAINS = [
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com",
    "live.com", "live.se", "hotmail.se", "outlook.se", "yahoo.se",
    "protonmail.com", "proton.me",
]


def _fix_email_domain(email: str) -> tuple:
    if "@" not in email:
        return email, None
    local, domain = email.rsplit("@", 1)
    domain_lower = domain.lower()

    if domain_lower in DOMAIN_TYPOS:
        fixed = DOMAIN_TYPOS[domain_lower]
        return f"{local}@{fixed}", fixed

    if domain_lower not in KNOWN_DOMAINS:
        for known in FUZZY_DOMAINS:
            if _levenshtein(domain_lower, known) == 1:
                return f"{local}@{known}", known

    return email, None


def _check_email_local_part(email: str, first_name: str, last_name: str) -> dict:
    if "@" not in email or not first_name or not last_name:
        return {"action": "ok"}

    local = email.rsplit("@", 1)[0].lower().strip()
    fn = first_name.lower().strip()
    ln = last_name.lower().strip()

    if len(fn) < 2 or len(ln) < 2 or len(local) < 2:
        return {"action": "ok"}

    patterns = [
        f"{fn}.{ln}", f"{fn}-{ln}", f"{fn}_{ln}", f"{fn}{ln}",
        f"{ln}.{fn}", f"{ln}-{fn}", f"{ln}_{fn}", f"{ln}{fn}",
        fn,
    ]

    best_dist = float("inf")
    best_pattern = ""
    for pattern in patterns:
        dist = _levenshtein(local, pattern)
        if dist < best_dist:
            best_dist = dist
            best_pattern = pattern

    if best_dist == 1:
        return {"action": "auto-correct", "suggested_local": best_pattern,
                "details": f"'{local}' → '{best_pattern}' (distance=1)"}
    if best_dist == 2:
        return {"action": "flag", "suggested_local": best_pattern,
                "details": f"'{local}' → '{best_pattern}'? (distance=2)"}

    return {"action": "ok"}


def _ghl_headers_v3() -> dict:
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Version": "v3",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _update_ghl_contact(contact_id: str, updates: dict) -> bool:
    if not updates or not GHL_API_KEY:
        return False
    try:
        resp = requests.put(
            f"{GHL_API_BASE}/contacts/{contact_id}",
            headers={**_ghl_headers(), "Content-Type": "application/json"},
            json=updates, timeout=15,
        )
        resp.raise_for_status()
        logging.info(f"✅ Contact {contact_id} updated: {list(updates.keys())}")
        return True
    except Exception as e:
        logging.error(f"❌ Contact update failed for {contact_id}: {e}")
        return False


def _add_ghl_tags(contact_id: str, tags: list) -> bool:
    if not tags or not GHL_API_KEY:
        return False
    try:
        resp = requests.post(
            f"{GHL_API_BASE}/contacts/{contact_id}/tags",
            headers=_ghl_headers_v3(), json={"tags": tags}, timeout=15,
        )
        resp.raise_for_status()
        logging.info(f"🏷️ Tags added to {contact_id}: {tags}")
        return True
    except Exception as e:
        logging.error(f"❌ Tag addition failed for {contact_id}: {e}")
        return False


def _add_ghl_note(contact_id: str, body: str) -> bool:
    if not body or not GHL_API_KEY:
        return False
    try:
        resp = requests.post(
            f"{GHL_API_BASE}/contacts/{contact_id}/notes",
            headers={**_ghl_headers(), "Content-Type": "application/json"},
            json={"body": body}, timeout=15,
        )
        resp.raise_for_status()
        logging.info(f"📝 Note added to {contact_id}")
        return True
    except Exception as e:
        logging.error(f"❌ Note addition failed for {contact_id}: {e}")
        return False


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
    raw_tags = contact.get("tags") or []
    logging.info(f"🏷️ Raw tags for {email}: {raw_tags}")
    contact_tags = [t.lower() if isinstance(t, str) else "" for t in raw_tags]

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


@app.post("/webhook/new-contact")
async def webhook_new_contact(request: Request):
    """GHL ContactCreate webhook → validate names and email, update contact."""
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logging.info(f"📥 NEW CONTACT WEBHOOK: {json.dumps(raw, default=str)[:500]}")

    # Workflow webhook sends flat snake_case fields; also handle camelCase / nested
    contact_data = raw.get("contact") or raw
    first_name = contact_data.get("first_name") or contact_data.get("firstName") or ""
    last_name = contact_data.get("last_name") or contact_data.get("lastName") or ""
    email = (contact_data.get("email") or "").strip().lower()

    # Contact ID: try payload fields first, fall back to email lookup
    contact_id = (raw.get("contact_id") or raw.get("contactId")
                  or (raw.get("contact") or {}).get("id"))
    if not contact_id and email:
        looked_up = _lookup_ghl_contact(email=email)
        if looked_up:
            contact_id = looked_up.get("id")
    if not contact_id:
        raise HTTPException(status_code=400, detail="Missing contact ID and unable to look up by email")

    corrections: Dict[str, Any] = {}
    updates: Dict[str, str] = {}
    tags_to_add: List[str] = []
    notes: List[str] = []

    # 1. Name capitalization
    if first_name:
        fixed_fn = _capitalize_name(first_name)
        if fixed_fn != first_name:
            corrections["firstName"] = {"from": first_name, "to": fixed_fn}
            updates["firstName"] = fixed_fn
            first_name = fixed_fn

    if last_name:
        fixed_ln = _capitalize_name(last_name)
        if fixed_ln != last_name:
            corrections["lastName"] = {"from": last_name, "to": fixed_ln}
            updates["lastName"] = fixed_ln
            last_name = fixed_ln

    if "firstName" in updates or "lastName" in updates:
        tags_to_add.append("name-corrected")
        logging.info(f"[CORRECTION] NAME {contact_id}: "
                     f"{corrections.get('firstName', {}).get('from', '')} → {corrections.get('firstName', {}).get('to', '')} | "
                     f"{corrections.get('lastName', {}).get('from', '')} → {corrections.get('lastName', {}).get('to', '')}")

    # 2. Email domain correction
    if email:
        fixed_email, corrected_domain = _fix_email_domain(email)
        if corrected_domain:
            original_domain = email.rsplit("@", 1)[1]
            corrections["emailDomain"] = {"from": original_domain, "to": corrected_domain}
            email = fixed_email
            tags_to_add.append("email-corrected")
            logging.info(f"[CORRECTION] DOMAIN {contact_id}: {original_domain} → {corrected_domain}")

        # 3. Email local part validation (uses corrected names for comparison)
        local_check = _check_email_local_part(email, first_name, last_name)
        corrections["emailLocalPart"] = local_check

        if local_check["action"] == "auto-correct":
            domain = email.rsplit("@", 1)[1]
            original_local = email.rsplit("@", 1)[0]
            email = f"{local_check['suggested_local']}@{domain}"
            if "email-corrected" not in tags_to_add:
                tags_to_add.append("email-corrected")
            logging.info(f"[CORRECTION] LOCAL {contact_id}: {original_local} → {local_check['suggested_local']} ({local_check.get('details', '')})")

        elif local_check["action"] == "flag":
            domain = email.rsplit("@", 1)[1]
            suggested = f"{local_check['suggested_local']}@{domain}"
            tags_to_add.append("email-review-needed")
            logging.info(f"[CORRECTION] FLAGGED {contact_id}: {email} — suggested {suggested} ({local_check.get('details', '')})")
            notes.append(
                f"⚠️ Possible email typo detected:\n"
                f"Current: {email}\n"
                f"Suggested: {suggested}\n"
                f"Details: {local_check['details']}\n"
                f"Contact: {first_name} {last_name}"
            )

        if email != (contact_data.get("email") or "").strip().lower():
            updates["email"] = email

    # 4. Apply updates
    result: Dict[str, Any] = {
        "status": "success",
        "contactId": contact_id,
        "corrections": corrections,
        "tags_added": tags_to_add,
    }

    if updates:
        result["contact_updated"] = _update_ghl_contact(contact_id, updates)
    else:
        result["contact_updated"] = False
        logging.info(f"✨ No corrections needed for {contact_id}")

    if tags_to_add:
        result["tags_result"] = _add_ghl_tags(contact_id, tags_to_add)

    for note in notes:
        result["note_added"] = _add_ghl_note(contact_id, note)

    logging.info(f"📋 Validation complete for {contact_id}: {json.dumps(result, default=str)}")
    return result


# --- Monthly Rollover Helpers ---

def _get_custom_values(location_id: str, api_key: str) -> list:
    resp = requests.get(
        f"{GHL_API_BASE}/locations/{location_id}/customValues",
        headers={"Authorization": f"Bearer {api_key}", "Version": "2021-07-28", "Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("customValues", [])


def _update_custom_value(location_id: str, value_id: str, name: str, value: str, api_key: str) -> bool:
    try:
        resp = requests.put(
            f"{GHL_API_BASE}/locations/{location_id}/customValues/{value_id}",
            headers={"Authorization": f"Bearer {api_key}", "Version": "2021-07-28",
                     "Accept": "application/json", "Content-Type": "application/json"},
            json={"name": name, "value": value}, timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"Failed to update custom value '{name}': {e}")
        return False


def _search_contacts_by_tag(tag: str, location_id: str, api_key: str) -> list:
    headers = {"Authorization": f"Bearer {api_key}", "Version": "v3",
               "Accept": "application/json", "Content-Type": "application/json"}
    contacts = []
    search_after = None
    while True:
        body: Dict[str, Any] = {
            "locationId": location_id,
            "pageLimit": 500,
            "filters": [{"field": "tags", "operator": "eq", "value": tag}],
            "fastPagination": True,
        }
        if search_after:
            body["searchAfter"] = search_after
        else:
            body["page"] = 1

        resp = requests.post(f"{GHL_API_BASE}/contacts/search", headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("contacts", [])
        if not batch:
            break
        contacts.extend(batch)
        if not data.get("hasMore", False):
            break
        search_after = batch[-1].get("searchAfter")
        if not search_after:
            break
    return contacts


def _remove_tag_from_contact(contact_id: str, tag: str, api_key: str) -> bool:
    try:
        resp = requests.delete(
            f"{GHL_API_BASE}/contacts/{contact_id}/tags",
            headers={"Authorization": f"Bearer {api_key}", "Version": "v3",
                     "Accept": "application/json", "Content-Type": "application/json"},
            json={"tags": [tag]}, timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"Failed to remove tag '{tag}' from {contact_id}: {e}")
        return False


def _remove_tag_from_all_contacts(tag: str, location_id: str, api_key: str, location_name: str) -> dict:
    contacts = _search_contacts_by_tag(tag, location_id, api_key)
    removed, failed = 0, 0
    for i, c in enumerate(contacts):
        if _remove_tag_from_contact(c["id"], tag, api_key):
            removed += 1
        else:
            failed += 1
        if (i + 1) % 80 == 0:
            time.sleep(1)
    logging.info(f"[ROLLOVER] {location_name} tag '{tag}': {removed}/{len(contacts)} removed")
    return {"found": len(contacts), "removed": removed, "failed": failed}


@app.post("/admin/monthly-rollover")
async def monthly_rollover(request: Request):
    """One-click monthly rollover: update custom values, strip tags, create sheet tab."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    summit_date_str = body.get("summit_start_date")
    if not summit_date_str:
        raise HTTPException(status_code=400, detail="summit_start_date is required (YYYY-MM-DD)")
    try:
        from datetime import date as date_type
        summit_date = datetime.strptime(summit_date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="summit_start_date must be YYYY-MM-DD format")

    summit_overlap = body.get("summit_overlap", False)
    bonus_value = body.get("bonus_value")
    dry_run = body.get("dry_run", True)

    month_swe = SWEDISH_MONTHS[summit_date.month - 1]
    day_1_date = str(summit_date.day)
    day_1_day = SWEDISH_DAYS[summit_date.weekday()]
    day_3_date = str((summit_date + timedelta(days=2)).day)
    day_5_date = str((summit_date + timedelta(days=4)).day)
    sheet_tab = ENGLISH_MONTHS_SHORT[summit_date.month - 1]
    overlap_str = "TRUE" if summit_overlap else "FALSE"

    logging.info(f"{'[DRY RUN]' if dry_run else '[LIVE]'} Monthly rollover: "
                 f"{month_swe} {day_1_date} ({day_1_day}) overlap={overlap_str}")

    calculated = {
        "month": month_swe, "day_1_date": day_1_date, "day_1_day": day_1_day,
        "day_3_date": day_3_date, "day_5_date": day_5_date,
        "overlap": overlap_str, "sheet_tab": sheet_tab,
    }
    results: Dict[str, Any] = {"dry_run": dry_run, "calculated": calculated, "carl": {}, "maria": {}}

    carl_custom_value_updates = {
        "CURRENT_SUMMIT_MONTH": month_swe,
        "CURRENT_SUMMIT_DAY_1_DATE": day_1_date,
        "CURRENT_SUMMIT_DAY_1_DAY": day_1_day,
        "CURRENT_SUMMIT_DAY_3_DATE": day_3_date,
        "CURRENT_SUMMIT_DAY_5_DATE": day_5_date,
        "CURRENT_SUMMIT_OVERLAP_IN_MONTH": overlap_str,
    }
    if bonus_value:
        carl_custom_value_updates["CURRENT_SUMMIT_BONUS_VALUE"] = bonus_value

    maria_custom_value_updates = {
        "LATE_REGISTRATION_PERIOD": "FALSE",
    }

    # --- DRY RUN: show what would happen ---
    if dry_run:
        for loc in GHL_LOCATIONS:
            if not loc["api_key"]:
                results[loc["name"]] = {"error": "API key not configured"}
                continue
            try:
                cv_list = _get_custom_values(loc["location_id"], loc["api_key"])
                relevant = {cv["name"]: cv["value"] for cv in cv_list
                            if "SUMMIT" in cv["name"].upper() or "LATE_REGISTRATION" in cv["name"].upper()}
                results[loc["name"]]["current_values"] = relevant
            except Exception as e:
                results[loc["name"]]["values_error"] = str(e)

            tag_counts = {}
            for tag in ROLLOVER_TAGS_TO_REMOVE:
                try:
                    contacts = _search_contacts_by_tag(tag, loc["location_id"], loc["api_key"])
                    tag_counts[tag] = len(contacts)
                except Exception as e:
                    tag_counts[tag] = f"error: {e}"
            results[loc["name"]]["contacts_to_untag"] = tag_counts

        updates_preview = {"carl": carl_custom_value_updates, "maria": maria_custom_value_updates}
        results["planned_updates"] = updates_preview
        results["planned_sheet_tab"] = sheet_tab
        return results

    # --- LIVE EXECUTION ---

    # 1. Carl's custom values
    try:
        carl_cv = _get_custom_values(GHL_LOCATION_ID, GHL_API_KEY)
        carl_cv_map = {cv["name"]: cv for cv in carl_cv}
        cv_results = {}
        for name, new_val in carl_custom_value_updates.items():
            cv = carl_cv_map.get(name)
            if not cv:
                cv_results[name] = "not found"
                continue
            old_val = cv["value"]
            ok = _update_custom_value(GHL_LOCATION_ID, cv["id"], name, new_val, GHL_API_KEY)
            cv_results[name] = f"{old_val} -> {new_val}" if ok else f"FAILED ({old_val} -> {new_val})"
        results["carl"]["custom_values"] = cv_results
    except Exception as e:
        results["carl"]["custom_values_error"] = str(e)

    # 2. Maria's custom values
    if GHL_API_KEY_MARIA and GHL_LOCATION_ID_MARIA:
        try:
            maria_cv = _get_custom_values(GHL_LOCATION_ID_MARIA, GHL_API_KEY_MARIA)
            maria_cv_map = {cv["name"]: cv for cv in maria_cv}
            cv_results = {}
            for name, new_val in maria_custom_value_updates.items():
                cv = maria_cv_map.get(name)
                if not cv:
                    cv_results[name] = "not found"
                    continue
                old_val = cv["value"]
                ok = _update_custom_value(GHL_LOCATION_ID_MARIA, cv["id"], name, new_val, GHL_API_KEY_MARIA)
                cv_results[name] = f"{old_val} -> {new_val}" if ok else f"FAILED ({old_val} -> {new_val})"
            results["maria"]["custom_values"] = cv_results
        except Exception as e:
            results["maria"]["custom_values_error"] = str(e)

    # 3. Remove tags from all contacts (both locations)
    for loc in GHL_LOCATIONS:
        if not loc["api_key"]:
            results[loc["name"]]["tags"] = "API key not configured"
            continue
        tag_results = {}
        for tag in ROLLOVER_TAGS_TO_REMOVE:
            try:
                tag_results[tag] = _remove_tag_from_all_contacts(
                    tag, loc["location_id"], loc["api_key"], loc["name"])
            except Exception as e:
                tag_results[tag] = f"error: {e}"
        results[loc["name"]]["tags"] = tag_results

    # 4. Sheet tab creation (logged — automated via Google Sheets API separately)
    results["sheet"] = {"tab_name": sheet_tab, "spreadsheet_id": MONTHLY_ITERATION_SHEET_ID,
                        "note": "Create tab via Google Sheets API or MCP tools"}

    logging.info(f"[ROLLOVER] Complete: {json.dumps(results, default=str)[:2000]}")
    return results


@app.get("/health-check")
def health_check():
    print("💓 Health check heartbeat", flush=True)
    return {"status": "ok", "mode": "Live"}