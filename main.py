import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import hashlib
import os
import re
import ipaddress

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

FB_PIXEL_ID = os.getenv("FB_PIXEL_ID", "YOUR_PIXEL_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
CAPI_URL = f"https://graph.facebook.com/v22.0/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"

landing_page_domain = "https://masterclass.carlhelgesson.com"
CLOUDFLARE_PAGES_DOMAIN_PURCHASE = os.getenv("CLOUDFLARE_PAGES_DOMAIN_PURCHASE", "YOUR_CF_DOMAIN_PURCHASE")
CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT = os.getenv("CLOUDFLARE_PAGES_DOMAIN_INITIATE_CHECKOUT", "YOUR_CF_DOMAIN_INITIATE_CHECKOUT")

FBP_REGEX = re.compile(r'^fb\.1\.\d+\.\d+$')

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        landing_page_domain,
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
    event_source_url: str
    action_source: str
    user_data: dict
    custom_data: dict

def hash_data(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

@app.post("/process-event")
def process_event(payload: ClientPayload, request: Request):
    """
    IP and User-Agent are always extracted server-side, ignoring
    any IP/User-Agent that might come from the client script.
    """
    logging.info("Received event payload: %s", payload.dict())

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

    logging.info("Hashed email: %s, first_name: %s, last_name: %s",
                 hashed_email, hashed_first_name, hashed_last_name)

    # 5) Build final Meta CAPI payload
    meta_payload = {
        "data": [
            {
                "event_name": payload.event_name,
                "event_time": payload.event_time,
                "event_source_url": payload.event_source_url,
                "action_source": payload.action_source,
                "user_data": {
                    "em": hashed_email,
                    "fn": hashed_first_name,
                    "ln": hashed_last_name,
                    "client_ip_address": client_ip,
                    "client_user_agent": server_user_agent,
                    "fbc": fbc,
                    "fbp": fbp
                },
                "custom_data": payload.custom_data
            }
        ]
    }

    logging.info("Built Meta CAPI payload: %s", meta_payload)

    # 6) Send to Meta Conversions API
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
