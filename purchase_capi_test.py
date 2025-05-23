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
GHL_WEBHOOK = "https://services.leadconnectorhq.com/hooks/kFKnF888dp7eKChjLxb9/webhook-trigger/16703c4e-0489-4128-a8a4-aa085b56881d"

landing_page_domain = "https://masterclass.carlhelgesson.com"
CLOUDFLARE_PAGES_DOMAIN_LEAD = os.getenv("CLOUDFLARE_PAGES_DOMAIN_LEAD", "YOUR_CF_DOMAIN_LEAD")
CLOUDFLARE_PAGES_DOMAIN_PURCHASE = os.getenv("CLOUDFLARE_PAGES_DOMAIN_PURCHASE", "YOUR_CF_DOMAIN_PURCHASE")
CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT = os.getenv("CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT", "YOUR_CF_DOMAIN_INITIATE_CHECKOUT")

FBP_REGEX = re.compile(r'^fb\.1\.\d+\.\d+$')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        landing_page_domain,
        CLOUDFLARE_PAGES_DOMAIN_LEAD,
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
    Handles events like Lead and Purchase, sending data to Meta CAPI, GA4, and GHL.

    IP and User-Agent are always extracted server-side, ignoring
    any IP/User-Agent that might come from the client script.
    """
    logging.info("Received event payload: %s", payload.model_dump())

    # 1) Pull fbc/fbp from the request body if present
    #    But ignore any IP or user_agent from the client side
    fbc = payload.user_data.get("fbc", "")
    fbp = payload.user_data.get("fbp", "")

    # 2) Server-side extraction of IP and User-Agent
    #    A) Use X-Forwarded-For if present, else fallback to request.client.host
    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    if x_forwarded_for:
        ip_list = [ip.strip() for ip in x_forwarded_for.split(",")]
        client_ip = ip_list[0] if ip_list else request.client.host
    else:
        client_ip = request.client.host if request.client else ""

    #    B) Force user_agent from request headers
    server_user_agent = request.headers.get("user-agent", "")

    logging.info("Server-extracted IP: %s, user-agent: %s, fbc: %s, fbp: %s",
                 client_ip, server_user_agent, fbc, fbp)

    # 3) Validate IP & fbp
    try:
        ipaddress.ip_address(client_ip)
    except ValueError:
        logging.warning("Invalid IP address: %s. Setting to empty string.", client_ip)
        client_ip = ""
    if not FBP_REGEX.match(fbp):
        if fbp:  # only log if non-empty
            logging.warning("Invalid _fbp format: %s. Setting to empty string.", fbp)
        fbp = ""

    # 4) Hash user’s name/email from the request body
    hashed_email = hash_data(payload.user_data.get("email", ""))
    hashed_first_name = hash_data(payload.user_data.get("first_name", ""))
    hashed_last_name = hash_data(payload.user_data.get("last_name", ""))
    hashed_phone = hash_data(payload.user_data.get("phone", ""))

    logging.info("Hashed email: %s, first_name: %s, last_name: %s, phone: %s",
                 hashed_email, hashed_first_name, hashed_last_name, hashed_phone)

    # 5) Handle custom_data if not provided
    custom_data = payload.custom_data if payload.custom_data else {}

    # Ensure the 'value' field in custom_data is numeric
    if "value" in custom_data:
        try:
            custom_data["value"] = float(custom_data["value"])
        except ValueError:
            logging.warning("Invalid value for 'custom_data.value': %s. Setting to 0.", custom_data["value"])
            custom_data["value"] = 0.0

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
                    "client_user_agent": server_user_agent,
                    "fbc": fbc,
                    "fbp": fbp
                },
                "custom_data": custom_data
            }
        ]
    }

    # Conditionally include external_id (GHL Contact ID)
    ghl_contact_id = payload.user_data.get("ghl_contact_id", None)  # Assuming GHL Contact ID is passed in user_data
    if ghl_contact_id:
        hashed_external_id = hash_data(ghl_contact_id)
        meta_payload["data"][0]["user_data"]["external_id"] = hashed_external_id
        logging.info("Included external_id (GHL Contact ID): %s", hashed_external_id)

    # Only include event_source_url if it is provided
    if payload.event_source_url:
        meta_payload["data"][0]["event_source_url"] = payload.event_source_url

    logging.info("Built Meta CAPI payload: %s", meta_payload)

    # 7) Build GHL payload
    ghl_payload = {
        "email": payload.user_data.get("email", ""), # To match contact in GHL
        "fbc": fbc,
        "fbp": fbp,
        "hashed_email": hashed_email,
        "hashed_first_name": hashed_first_name,
        "hashed_last_name": hashed_last_name,
        "hashed_phone": hashed_phone
    }

    logging.info("Built GHL payload: %s", ghl_payload)

    # # 8) Build final GA4 payload
    # ga_payload = {
    #     "client_id": fbp if fbp else "default_client_id",  # Fallback to a default value
    #     "events": [
    #         {
    #             "name": "generate_lead",
    #             "params": {
    #                 "event_time": payload.event_time,
    #                 "lead_source": payload.user_data.get("utm_source", ""),
    #                 "source_medium": payload.user_data.get("utm_medium", ""),
    #                 "ad_id": payload.user_data.get("utm_content", ""),
    #                 "currency": "SEK",
    #                 "value": 0
    #             }
    #         }
    #     ]
    # }

    # logging.info("Built GA4 payload: %s", ga_payload)

    # 9) Send to Meta Conversions API
    logging.info("Here's where we would have sent to Meta CAPI, awesome!")
    # try:
    #     response = requests.post(CAPI_URL, json=meta_payload)
    #     response.raise_for_status()
    #     meta_response = response.json()
    #     meta_status_code = response.status_code
    #     logging.info("Meta CAPI response: %s", meta_response)
    # except requests.exceptions.RequestException as e:
    #     logging.error("Meta CAPI request failed: %s", str(e))
    #     raise HTTPException(
    #         status_code=500,
    #         detail=f"Meta CAPI request failed: {str(e)}"
    #     )
    
    # # 10) Send to GHL
    # try:
    #     ghl_response = requests.post(
    #         GHL_WEBHOOK,
    #         json=ghl_payload
    #     )
    #     ghl_response.raise_for_status()
    #     ghl_response_json = ghl_response.json()
    #     ghl_status_code = ghl_response.status_code
    #     logging.info("GHL response: %s", ghl_response_json)
    # except requests.exceptions.RequestException as e:
    #     logging.error("GHL request failed: %s", str(e))
    #     raise HTTPException(
    #         status_code=500,
    #         detail=f"GHL request failed: {str(e)}"
    #     )

    # # 11) Send to GA4 Measurement Protocol
    # try:
    #     ga_response = requests.post(GA4_URL, json=ga_payload)
    #     ga_response.raise_for_status()
    #     ga_response_json = ga_response.json()
    #     ga_status_code = ga_response.status_code
    #     logging.info("GA4 response: %s", ga_response_json)
    # except requests.exceptions.RequestException as e:
    #     logging.error("GA4 request failed: %s", str(e))
    #     logging.error("GA4 response content: %s", ga_response.text)  # Log raw response
    #     raise HTTPException(
    #         status_code=500,
    #         detail=f"GA4 request failed: {str(e)}"
    #     )
    
   # Final return with all responses
    return {
        "meta_response": {
            "status_code": "Hopefully 200!",
            "response": "Good job team, this is my favorite one so far"
        },
        # "ghl_response": {
        #     "status_code": ghl_status_code,
        #     "response": ghl_response_json
        # }
    }
