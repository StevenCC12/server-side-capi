from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import hashlib
import time
import requests
import os
import random
import string
import json
from dotenv import load_dotenv

# Load environment variables from .env (for security)
load_dotenv()

# Meta CAPI Credentials
FB_PIXEL_ID = os.getenv("FB_PIXEL_ID", "your_pixel_id")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "your_access_token")
CAPI_URL = f"https://graph.facebook.com/v22.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"

# Other variables
fallback_URL = "https://woocommerce.build/lp-page"
landing_page_domain = "https://woocommerce.build"
cloudflare_pages_domain_initiatecheckout = "https://25343b56.amazon-challenge-serverside-capi.pages.dev"
cloudflare_pages_domain_purchase = "https://15550c2e.amazon-challenge-serverside-capi-purchase.pages.dev"

# FastAPI Instance
app = FastAPI()

# CORS Middleware for Render
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        landing_page_domain,  # Landing page domain
        # Cloudflare Pages domains
        cloudflare_pages_domain_initiatecheckout,  
        cloudflare_pages_domain_purchase
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Unified Request Model for Both Events
class ClientPayload(BaseModel):
    event_name: str
    event_time: int
    event_source_url: str
    action_source: str
    event_id: str
    user_data: dict
    custom_data: dict

# Hashing Function
def hash_data(value: str) -> str:
    """Returns SHA256 hash of user data (required for CAPI)."""
    return hashlib.sha256(value.encode()).hexdigest() if value else ""

# Event ID Generator Function
def generate_event_id():
    return 'server-' + ''.join(random.choices(string.ascii_letters + string.digits, k=10)) + '-' + str(int(time.time()))

@app.post("/process-initiate-checkout-event-data/")
async def process_checkout(request: Request, data: ClientPayload):
    return process_event(request, data)

@app.post("/process-purchase-event-data/")
async def process_purchase(request: Request, data: ClientPayload):
    return process_event(request, data)

def process_event(request: Request, data: ClientPayload):
    """Handles both InitiateCheckout & Purchase event processing."""
    try:
        print("✅ Received Webhook Data from Client:")
        print(data.dict())  # Log raw input data

        # Ensure event_id is not null (use client-side value or generate fallback)
        event_id = data.event_id if data.event_id else generate_event_id()

        # Ensure fresh UNIX timestamp (use client-side value or generate fallback)
        current_time = int(time.time())
        event_time = data.event_time if (current_time - 172800 < data.event_time < current_time + 300) else current_time 
        # This ensures event_time is within the last 48 hours or the next 5 minutes

        # Extract IP Address from Request, Click ID and Browser ID from Cookies and User Agent
        client_ip = request.client.host if request.client else ""
        fbc = data.user_data.get("fbc", "") if fbc.startswith("fb.1.") else ""
        fbp = data.user_data.get("fbp", "") if fbp.startswith("fb.1.") else "",
        print(f"🔹 Extracted Client IP: {client_ip}")
        print(f"🔹 Extracted FBC: {fbc}")
        print(f"🔹 Extracted FBP: {fbp}")

        # Hash user data
        hashed_email = hash_data(data.user_data.get("email", ""))
        hashed_first_name = hash_data(data.user_data.get("first_name", ""))
        hashed_last_name = hash_data(data.user_data.get("last_name", ""))
        hashed_fbc = hash_data(fbc)
        hashed_fbp = hash_data(fbp)
        hashed_ip = hash_data(client_ip)
        hashed_user_agent = hash_data(data.user_data.get("user_agent", ""))

        print(f"🔹 Fresh UNIX Timestamp: {event_time}")
        print(f"🔹 Hashed Email: {hashed_email}")
        print(f"🔹 Hashed First Name: {hashed_first_name}")
        print(f"🔹 Hashed Last Name: {hashed_last_name}")

        # Prepare payload for Meta CAPI
        payload = {
            "data": [{
                "event_name": data.event_name,
                "event_time": event_time,
                "event_source_url": data.event_source_url,
                "action_source": data.action_source,
                "event_id": event_id,
                "user_data": {
                    "em": hashed_email,
                    "fn": hashed_first_name,
                    "ln": hashed_last_name,
                    "fbc": hashed_fbc,
                    "fbp": hashed_fbp,
                    "client_ip_address": hashed_ip,
                    "client_user_agent": hashed_user_agent
                },
                "custom_data": data.custom_data  # Use the client-provided custom data
            }]
        }

        # Log final payload before sending with json.dumps() for cleaner debugging
        print("📤 Sending Payload to Meta CAPI:\n", json.dumps(payload, indent=2))

        # Send request to Meta CAPI
        response = requests.post(CAPI_URL, json=payload)
        response.raise_for_status()  # 🚨 Triggers error if response is 4xx or 5xx
        response_data = response.json()  # Parse response as JSON

        # ✅ Log response from Meta for debugging
        print(f"✅ Response from Meta CAPI: {response_data}")

        # ✅ Return status, event_time, and response for debugging
        return {
            "status": response.status_code,
            "response": response_data,
            "event_time": event_time
        }

    except requests.exceptions.RequestException as e:
        print(f"❌ Error sending event to Meta CAPI: {e}")  # Log error
        raise HTTPException(status_code=500, detail=f"Meta CAPI request failed: {str(e)}")  # Return error response