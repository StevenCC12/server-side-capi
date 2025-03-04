import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import hashlib
import os
import re, ipaddress

# Configure logging (adjust level and format as needed)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# -------------------------------
# 1. Basic Setup
# -------------------------------
FB_PIXEL_ID = os.getenv("FB_PIXEL_ID", "YOUR_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
CAPI_URL = f"https://graph.facebook.com/v22.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"

fallback_URL = "https://challenge.carlhelgesson.com/5-dagars-challenge"
landing_page_domain = "https://challenge.carlhelgesson.com"
CLOUDFLARE_PAGES_DOMAIN_PURCHASE = os.getenv("CLOUDFLARE_PAGES_DOMAIN_PURCHASE", "YOUR_CF_DOMAIN_PURCHASE")
CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT = os.getenv("CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT", "YOUR_CF_DOMAIN_INITIATE_CHECKOUT")

# Regex for _fbp format: fb.1.<timestamp>.<randomNumber>
FBP_REGEX = re.compile(r'^fb\.1\.\d+\.\d+$')

app = FastAPI()

# CORS Middleware for Render
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # landing_page_domain,  # Landing page domain  
        # CLOUDFLARE_PAGES_DOMAIN_PURCHASE,
        # CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT
        "*" # Allow all origins while testing
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
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

# -------------------------------
# 4. Conversion Event Endpoint
# -------------------------------
@app.post("/process-event")
def process_event(payload: ClientPayload, request: Request):
    logging.info("Received event payload: %s", payload.dict())

    # 4.1 Extract user data in plain text
    client_ip = request.client.host if request.client else ""
    user_agent = payload.user_data.get("user_agent", "")
    fbc = payload.user_data.get("fbc", "")
    fbp = payload.user_data.get("fbp", "")
    logging.info("Extracted client_ip: %s, user_agent: %s, fbc: %s, fbp: %s", client_ip, user_agent, fbc, fbp)

    # 4.2 Validate IP address and _fbp format
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        logging.warning("Invalid client IP address: %s. Setting to empty string.", client_ip)
        client_ip = ""
    if not FBP_REGEX.match(fbp):
        logging.warning("Invalid _fbp format: %s. Setting to empty string.", fbp)
        fbp = ""

    # 4.3 Hash only email, first_name, last_name
    hashed_email = hash_data(payload.user_data.get("email", ""))
    hashed_first_name = hash_data(payload.user_data.get("first_name", ""))
    hashed_last_name = hash_data(payload.user_data.get("last_name", ""))
    logging.info("Hashed email: %s, first_name: %s, last_name: %s", hashed_email, hashed_first_name, hashed_last_name)

    # 4.4 Build final Meta CAPI payload
    meta_payload = {
        "data": [
            {
                "event_name": payload.event_name,
                "event_time": payload.event_time,
                "event_source_url": payload.event_source_url,
                "action_source": payload.action_source,
                "user_data": {
                    "em": hashed_email,          # Email (hashed)
                    "fn": hashed_first_name,     # First name (hashed)
                    "ln": hashed_last_name,      # Last name (hashed)
                    "client_ip_address": client_ip,  # Plain text
                    "client_user_agent": user_agent, # Plain text
                    "fbc": fbc,                  # Plain text
                    "fbp": fbp                   # Plain text
                },
                "custom_data": payload.custom_data
            }
        ]
    }
    logging.info("Built Meta CAPI payload: %s", meta_payload)

    # 4.5 Send to Meta Conversions API
    try:
        response = requests.post(CAPI_URL, json=meta_payload)
        response.raise_for_status()
        meta_response = response.json()
        logging.info("Meta CAPI response: %s", meta_response)
        return {
            "status": response.status_code,
            "meta_response": meta_response
        }
    except requests.exceptions.RequestException as e:
        logging.error("Meta CAPI request failed: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Meta CAPI request failed: {str(e)}"
        )
