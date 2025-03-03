from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import hashlib
import os

# -------------------------------
# 1. Basic Setup
# -------------------------------
FB_PIXEL_ID = os.getenv("FB_PIXEL_ID", "YOUR_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
CAPI_URL = f"https://graph.facebook.com/v22.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"

fallback_URL = "https://challenge.carlhelgesson.com/5-dagars-challenge"
landing_page_domain = "https://challenge.carlhelgesson.com"
cloudflare_pages_domain_purchase = "https://ccff1ad5.day-1-pruchase-tracking.pages.dev"

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
class PurchasePayload(BaseModel):
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
# 4. Purchase Endpoint
# -------------------------------
@app.post("/purchase")
def purchase(payload: PurchasePayload, request: Request):
    """
    Receives the Purchase event from the Thank You page
    and forwards it to Meta via the Conversions API.
    """
    # 4.1 Extract IP from the request
    client_ip = request.client.host  # e.g. "123.45.67.89"

    # 4.2 Hash user data
    hashed_email = hash_data(payload.user_data.get("email", ""))
    hashed_user_agent = hash_data(payload.user_data.get("user_agent", ""))

    # 4.3 Build final Meta CAPI payload
    meta_payload = {
        "data": [
            {
                "event_name": payload.event_name,
                "event_time": payload.event_time,
                "event_source_url": payload.event_source_url,
                "action_source": payload.action_source,
                "event_id": payload.event_id,
                "user_data": {
                    # FB recommends using specific keys like "em", "ph", "fn", "ln", etc.
                    "em": hashed_email,
                    "client_ip_address": client_ip,
                    "client_user_agent": hashed_user_agent
                },
                "custom_data": payload.custom_data
            }
        ]
    }

    # 4.4 Send to Meta Conversions API
    try:
        response = requests.post(CAPI_URL, json=meta_payload)
        response.raise_for_status()  # Raises an exception if 4xx/5xx
        return {
            "status": response.status_code,
            "meta_response": response.json()
        }
    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "message": str(e)
        }
