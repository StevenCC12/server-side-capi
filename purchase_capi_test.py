import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import hashlib
import os
import re
import json
import ipaddress
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

FB_PIXEL_ID = os.getenv("FB_PIXEL_ID", "YOUR_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
CAPI_URL = f"https://graph.facebook.com/v19.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}" # Updated to v19.0, adjust if needed
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
    event_source_url: Optional[str] = None
    action_source: str
    user_data: dict # Expects fields like email, first_name, user_agent (original browser)
    custom_data: Optional[dict] = None

def hash_data(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

@app.post("/process-event") # Ensure this path matches your actual endpoint if it had a trailing slash
def process_event(payload: ClientPayload, request: Request):
    logging.info("Received event payload: %s", payload.model_dump())

    # 1) Pull fbc/fbp from the request body if present
    fbc_val = payload.user_data.get("fbc", "")
    fbp_val = payload.user_data.get("fbp", "")

    if fbc_val is None or (isinstance(fbc_val, str) and fbc_val.lower() == 'null'):
        fbc_val = ""
    if fbp_val is None or (isinstance(fbp_val, str) and fbp_val.lower() == 'null'):
        fbp_val = ""

    # 2a) Server-side extraction of IP
    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    if x_forwarded_for:
        ip_list = [ip.strip() for ip in x_forwarded_for.split(",")]
        client_ip = ip_list[0] if ip_list else (request.client.host if request.client else "")
    else:
        client_ip = request.client.host if request.client else ""

    # --- MODIFIED SECTION 2b: User Agent Handling ---
    # Priority 1: From payload.user_data.user_agent (this is where curl/JS sends original browser UA)
    client_user_agent = payload.user_data.get("user_agent", "") 

    # Priority 2: Fallback to payload.custom_data.user_agent_captured_client_side (your original check)
    if not client_user_agent:
        temp_custom_data_for_ua = payload.custom_data if payload.custom_data else {}
        if isinstance(temp_custom_data_for_ua, dict):
            client_user_agent = temp_custom_data_for_ua.get("user_agent_captured_client_side", "")
            
    # Priority 3: Fallback to the current request's User-Agent header (e.g., curl's UA)
    if not client_user_agent:
        client_user_agent = request.headers.get("user-agent", "")
    # --- END OF MODIFIED SECTION 2b ---

    logging.info("Server-extracted IP: %s, Determined client_user_agent for Meta: %s, fbc: %s, fbp: %s",
                 client_ip, client_user_agent, fbc_val, fbp_val)

    # 3) Validate IP & fbp
    try:
        if client_ip: # Only validate if client_ip is not an empty string
            ipaddress.ip_address(client_ip)
    except ValueError:
        logging.warning("Invalid IP address: %s. Setting to empty string.", client_ip)
        client_ip = ""

    if not FBP_REGEX.match(fbp_val):
        if fbp_val:
            logging.warning("Invalid _fbp format: %s. Setting to empty string.", fbp_val)
        fbp_val = ""

    # 4) Hash user’s PII from the request body
    hashed_email = hash_data(payload.user_data.get("email", ""))
    hashed_first_name = hash_data(payload.user_data.get("first_name", ""))
    hashed_last_name = hash_data(payload.user_data.get("last_name", ""))
    hashed_phone = hash_data(payload.user_data.get("phone", ""))
    hashed_country = hash_data(payload.user_data.get("country", "").lower() if payload.user_data.get("country") else "")
    hashed_city = hash_data(payload.user_data.get("city", ""))
    hashed_zip = hash_data(payload.user_data.get("zip", ""))

    logging.info("Hashed PII: em: %s, fn: %s, ln: %s, ph: %s",
                 "present" if hashed_email else "empty", 
                 "present" if hashed_first_name else "empty", 
                 "present" if hashed_last_name else "empty", 
                 "present" if hashed_phone else "empty")

    # --- MODIFIED SECTION 5: Clean custom_data ---
    # This section now directly and safely uses payload.custom_data
    final_cleaned_custom_data = {}
    source_custom_data_from_payload = payload.custom_data # This is Optional[dict]

    if source_custom_data_from_payload: # True if it's a non-empty dict
        for key, value in source_custom_data_from_payload.items():
            if isinstance(value, str) and value.lower() == 'null':
                # Do not add "null" strings to final_cleaned_custom_data
                pass
            elif value is not None: # Add if not Python None (and not string "null")
                final_cleaned_custom_data[key] = value
            # Python None values are skipped and thus omitted.

    # Ensure 'value' and 'currency' are correctly handled in final_cleaned_custom_data
    if "value" in final_cleaned_custom_data:
        try:
            current_val = final_cleaned_custom_data["value"]
            if current_val is not None and not isinstance(current_val, (int, float)):
                final_cleaned_custom_data["value"] = float(current_val)
            elif current_val is None:
                 final_cleaned_custom_data["value"] = 0.0
        except (ValueError, TypeError):
            if final_cleaned_custom_data.get("value") is not None:
                logging.warning(f"Invalid value for 'custom_data.value': {final_cleaned_custom_data.get('value')}. Setting to 0.0")
            final_cleaned_custom_data["value"] = 0.0
    
    if "currency" in final_cleaned_custom_data:
        current_curr = final_cleaned_custom_data["currency"]
        if (current_curr is None or \
            (isinstance(current_curr, str) and \
             (current_curr.lower() == 'null' or current_curr == ""))):
            final_cleaned_custom_data["currency"] = "SEK"
    
    # Omit any keys that might have become None during specific handling above (if any such logic was added)
    # or were None initially and we want to ensure they are removed.
    # The current loop logic already omits "null" strings and initial None values.
    final_cleaned_custom_data = {k: v for k, v in final_cleaned_custom_data.items() if v is not None}
    # --- END OF MODIFIED SECTION 5 ---

    # 6) Build final Meta CAPI payload
    meta_payload_user_data = {
        "client_ip_address": client_ip if client_ip else None, # Send None if empty for Meta to handle
        "client_user_agent": client_user_agent if client_user_agent else None, # Send None if empty
        "fbc": fbc_val if fbc_val else None,
        "fbp": fbp_val if fbp_val else None
    }
    # Add hashed PII if present
    if hashed_email: meta_payload_user_data["em"] = hashed_email
    if hashed_first_name: meta_payload_user_data["fn"] = hashed_first_name
    if hashed_last_name: meta_payload_user_data["ln"] = hashed_last_name
    if hashed_phone: meta_payload_user_data["ph"] = hashed_phone
    if hashed_country: meta_payload_user_data["country"] = hashed_country
    if hashed_city: meta_payload_user_data["ct"] = hashed_city
    if hashed_zip: meta_payload_user_data["zp"] = hashed_zip
    
    # Remove None values from user_data for cleaner payload to Meta
    meta_payload_user_data = {k: v for k, v in meta_payload_user_data.items() if v is not None}

    meta_payload_event_data = {
        "event_name": payload.event_name,
        "event_time": payload.event_time,
        "action_source": payload.action_source,
        "user_data": meta_payload_user_data,
        "custom_data": final_cleaned_custom_data # Use the fully cleaned custom_data
    }

    ghl_contact_id = payload.user_data.get("external_id", None) # Check if your JS sends this
    if ghl_contact_id:
        hashed_external_id = hash_data(ghl_contact_id)
        meta_payload_event_data["user_data"]["external_id"] = hashed_external_id
        logging.info("Included external_id (GHL Contact ID): %s", hashed_external_id)

    if payload.event_source_url:
        meta_payload_event_data["event_source_url"] = payload.event_source_url
    
    meta_payload = {"data": [meta_payload_event_data]}

    logging.info("Built Meta CAPI payload: %s", json.dumps(meta_payload, indent=2)) # Log the final payload

    # try:
    #     response = requests.post(CAPI_URL, json=meta_payload)
    #     response.raise_for_status()
    #     meta_response = response.json()
    #     meta_status_code = response.status_code
    #     logging.info("Meta CAPI response: %s", meta_response)
    # except requests.exceptions.RequestException as e:
    #     logging.error("Meta CAPI request failed: %s", str(e))
    #     error_detail = f"Meta CAPI request failed: {str(e)}"
    #     if hasattr(e, 'response') and e.response is not None:
    #         try:
    #             error_detail += f" - Response: {e.response.text}"
    #         except Exception:
    #             pass # Ignore if can't get response text
    #     raise HTTPException(
    #         status_code=500,
    #         detail=error_detail
    #     )

    # New logging for testing purposes
    logging.info("Event would be successfully sent to Meta under the exact same conditions it would be sent")

    return {
        "meta_response": {
            "status_code": 200,  # Return a successful status code to the client
            "response": {"message": "Test mode: Event not sent to Meta."}
        }
    }