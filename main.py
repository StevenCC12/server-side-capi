import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import hashlib
import os
import re
import ipaddress
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

FB_PIXEL_ID = os.getenv("FB_PIXEL_ID", "YOUR_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
CAPI_URL = f"https://graph.facebook.com/v22.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"
GA4_MEASUREMENT_ID = os.getenv("GA4_MEASUREMENT_ID", "YOUR_GA4_MEASUREMENT_ID")
GA4_API_SECRET = os.getenv("GA4_API_SECRET", "YOUR_GA4_API_SECRET")
GA4_URL = f"https://www.google-analytics.com/mp/collect?measurement_id={GA4_MEASUREMENT_ID}&api_secret={GA4_API_SECRET}"

landing_page_domain = "https://masterclass.carlhelgesson.com"
CLOUDFLARE_PAGES_DOMAIN_LEAD_OPT_IN = os.getenv("CLOUDFLARE_PAGES_DOMAIN_LEAD_OPT_IN", "YOUR_CF_DOMAIN_LEAD")
CLOUDFLARE_PAGES_DOMAIN_LEAD_THANK_YOU = os.getenv("CLOUDFLARE_PAGES_DOMAIN_LEAD_THANK_YOU", "YOUR_CF_DOMAIN_LEAD")
CLOUDFLARE_PAGES_DOMAIN_PURCHASE = os.getenv("CLOUDFLARE_PAGES_DOMAIN_PURCHASE", "YOUR_CF_DOMAIN_PURCHASE")
CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT = os.getenv("CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT", "YOUR_CF_DOMAIN_INITIATE_CHECKOUT")

FBP_REGEX = re.compile(r'^fb\.1\.\d+\.\d+$')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        landing_page_domain,
        CLOUDFLARE_PAGES_DOMAIN_LEAD_OPT_IN,
        CLOUDFLARE_PAGES_DOMAIN_LEAD_THANK_YOU,
        CLOUDFLARE_PAGES_DOMAIN_PURCHASE,
        CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ClientPayload(BaseModel):
    event_name: str
    event_time: int
    event_source_url: Optional[str] = None  # Make event_source_url optional
    action_source: str
    user_data: dict
    custom_data: Optional[dict] = None # Make custom_data optional, default to None

def hash_data(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

@app.post("/process-event")
def process_event(payload: ClientPayload, request: Request):
    """
    Handles events like Lead and Purchase, sending data to Meta CAPI.

    IP and User-Agent are always extracted server-side, ignoring
    any IP/User-Agent that might come from the client script.
    """
    logging.info("Received event payload: %s", payload.model_dump())

    # 1) Pull fbc/fbp from the request body if present
    fbc_val = payload.user_data.get("fbc", "")
    fbp_val = payload.user_data.get("fbp", "")

    # Fix for "null" string or None values
    if fbc_val is None or (isinstance(fbc_val, str) and fbc_val.lower() == 'null'):
        fbc_val = ""
    if fbp_val is None or (isinstance(fbp_val, str) and fbp_val.lower() == 'null'):
        fbp_val = ""

    # 2) Server-side extraction of IP
    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    if x_forwarded_for:
        ip_list = [ip.strip() for ip in x_forwarded_for.split(",")]
        client_ip = ip_list[0] if ip_list else request.client.host
    else:
        client_ip = request.client.host if request.client else ""

    # 2b) Use client-side user agent if provided in custom_data, else fallback to server-side
    custom_data = payload.custom_data if payload.custom_data else {}
    client_user_agent = ""
    if isinstance(custom_data, dict):
        client_user_agent = custom_data.get("user_agent_captured_client_side", "")
    if not client_user_agent:
        client_user_agent = request.headers.get("user-agent", "")

    logging.info("Server-extracted IP: %s, user-agent: %s, fbc: %s, fbp: %s",
                 client_ip, client_user_agent, fbc_val, fbp_val)

    # 3) Validate IP & fbp
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        logging.warning("Invalid IP address: %s. Setting to empty string.", client_ip)
        client_ip = ""
    if not FBP_REGEX.match(fbp_val):
        if fbp_val:  # only log if non-empty
            logging.warning("Invalid _fbp format: %s. Setting to empty string.", fbp_val)
        fbp_val = ""

    # 4) Hash user’s name/email and residence info from the request body
    hashed_email = hash_data(payload.user_data.get("email", ""))
    hashed_first_name = hash_data(payload.user_data.get("first_name", ""))
    hashed_last_name = hash_data(payload.user_data.get("last_name", ""))
    hashed_phone = hash_data(payload.user_data.get("phone", ""))

    hashed_country = hash_data(payload.user_data.get("country", "").lower())
    hashed_city = hash_data(payload.user_data.get("city", ""))
    hashed_zip = hash_data(payload.user_data.get("zip", ""))

    logging.info("Hashed email: %s, first_name: %s, last_name: %s, phone: %s",
                 hashed_email, hashed_first_name, hashed_last_name, hashed_phone)

    # 5) Clean custom_data: replace "null" strings with None, then omit keys with None
    cleaned_custom_data = {}
    if custom_data:
        for key, value in custom_data.items():
            if isinstance(value, str) and value.lower() == 'null':
                cleaned_custom_data[key] = None  # Or "", or omit by not adding
            elif value is not None:
                cleaned_custom_data[key] = value

    # Ensure 'value' and 'currency' are correctly handled if present
    if "value" in cleaned_custom_data:
        try:
            cleaned_custom_data["value"] = float(cleaned_custom_data["value"])
        except (ValueError, TypeError):
            if cleaned_custom_data["value"] is not None:
                logging.warning("Invalid value for 'custom_data.value': %s. Setting to 0.0", cleaned_custom_data["value"])
            cleaned_custom_data["value"] = 0.0
    if "currency" in cleaned_custom_data and (cleaned_custom_data["currency"] is None or (isinstance(cleaned_custom_data["currency"], str) and cleaned_custom_data["currency"].lower() == 'null')):
        cleaned_custom_data["currency"] = "SEK"

    # Omit keys with None value
    cleaned_custom_data = {k: v for k, v in cleaned_custom_data.items() if v is not None}

    # 6) Build final Meta CAPI payload
    meta_payload = {
        "data": [
            {
                "event_name": payload.event_name,
                "event_time": payload.event_time,
                "action_source": payload.action_source,
                "user_data": {
                    "em": hashed_email,
                    "fn": hashed_first_name,
                    "ln": hashed_last_name,
                    "ph": hashed_phone,
                    "client_ip_address": client_ip,
                    "client_user_agent": client_user_agent,
                    "fbc": fbc_val,
                    "fbp": fbp_val
                },
                "custom_data": cleaned_custom_data
            }
        ]
    }

    ghl_contact_id = payload.user_data.get("external_id", None)
    if ghl_contact_id:
        hashed_external_id = hash_data(ghl_contact_id)
        meta_payload["data"][0]["user_data"]["external_id"] = hashed_external_id
        logging.info("Included external_id (GHL Contact ID): %s", hashed_external_id)

    if payload.event_source_url:
        meta_payload["data"][0]["event_source_url"] = payload.event_source_url

    if hashed_country:
        meta_payload["data"][0]["user_data"]["country"] = hashed_country
    if hashed_city:
        meta_payload["data"][0]["user_data"]["ct"] = hashed_city
    if hashed_zip:
        meta_payload["data"][0]["user_data"]["zp"] = hashed_zip

    logging.info("Built Meta CAPI payload: %s", meta_payload)

    try:
        response = requests.post(CAPI_URL, json=meta_payload)
        response.raise_for_status()
        meta_response = response.json()
        meta_status_code = response.status_code
        logging.info("Meta CAPI response: %s", meta_response)
    except requests.exceptions.RequestException as e:
        logging.error("Meta CAPI request failed: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Meta CAPI request failed: {str(e)}"
        )

    return {
        "meta_response": {
            "status_code": meta_status_code,
            "response": meta_response
        }
    }
