from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import hashlib
import os
import re, ipaddress # To ensure correct format of _fbp and IP address

# -------------------------------
# 1. Basic Setup
# -------------------------------
FB_PIXEL_ID = os.getenv("FB_PIXEL_ID", "YOUR_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
CAPI_URL = f"https://graph.facebook.com/v22.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"

fallback_URL = "https://challenge.carlhelgesson.com/5-dagars-challenge"
landing_page_domain = "https://challenge.carlhelgesson.com"
cloudflare_pages_domain_purchase = "17ab41dc.day-1-pruchase-tracking.pages.dev"

# Regex for _fbp format: fb.1.<timestamp>.<randomNumber>
FBP_REGEX = re.compile(r'^fb\.1\.\d+\.\d+$')

app = FastAPI()

# CORS Middleware for Render
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        landing_page_domain,  # Landing page domain  
        cloudflare_pages_domain_purchase # Cloudflare Pages domains
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# 2. Data Model
# -------------------------------
class ClientPayload(BaseModel):
    event_name: str
    event_time: int
    event_source_url: str
    action_source: str
    event_id: str
    user_data: dict
    custom_data: dict

# -------------------------------
# 3. Hashing Function
# -------------------------------
def hash_data(value: str) -> str:
    """
    Simple SHA-256 hash for personally identifiable info
    like email, phone, name, etc. 
    """
    if not value:
        return ""
    # Lowercase + trim recommended for consistent hashing
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

# -------------------------------
# 4. Conversion Event Endpoint
# -------------------------------
@app.post("/process-event")
def process_event(payload: ClientPayload, request: Request):
    """
    Receives any event (e.g., Purchase or InitiateCheckout) from the client side,
    and forwards it to Meta via the Conversions API.
    """
    # 4.1 Extract user data in plain text
    client_ip = request.client.host if request.client else ""
    user_agent = payload.user_data.get("user_agent", "")
    fbc = payload.user_data.get("fbc", "")
    fbp = payload.user_data.get("fbp", "")

    # 4.2 Validate ip address and _fbp format
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        # Not a valid IPv4 or IPv6
        client_ip = ""

    if not FBP_REGEX.match(fbp):
        fbp = ""

    # 4.3 Hash only email, first_name, last_name
    hashed_email = hash_data(payload.user_data.get("email", ""))
    hashed_first_name = hash_data(payload.user_data.get("first_name", ""))
    hashed_last_name = hash_data(payload.user_data.get("last_name", ""))

    # 4.4 Build final Meta CAPI payload
    meta_payload = {
        "data": [
            {
                "event_name": payload.event_name,
                "event_time": payload.event_time,
                "event_source_url": payload.event_source_url,
                "action_source": payload.action_source,
                "event_id": payload.event_id,
                "user_data": {
                    # These fields should be hashed
                    "em": hashed_email,           # email
                    "fn": hashed_first_name,      # first name
                    "ln": hashed_last_name,       # last name

                    # These fields are plain text
                    "client_ip_address": client_ip,
                    "client_user_agent": user_agent,
                    "fbc": fbc,
                    "fbp": fbp
                },
                "custom_data": payload.custom_data
            }
        ]
    }

    # 4.5 Send to Meta Conversions API
    try:
        response = requests.post(CAPI_URL, json=meta_payload)
        response.raise_for_status()  # Raises an exception if 4xx/5xx
        return {
            "status": response.status_code,
            "meta_response": response.json()
        }
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Meta CAPI request failed: {str(e)}"
        )
