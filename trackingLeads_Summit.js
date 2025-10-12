// ===================================================================
//  Meta CAPI Event Tracking: GHL Lead Event (v3 - Merged)
//  Combines robust MutationObserver with a reusable send function.
// ===================================================================

// ---------------------------
// 1) Helper Functions
// ---------------------------

function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

function extractUTMParams() {
    const params = new URLSearchParams(window.location.search);
    return {
        utm_source: params.get('utm_source') || "",
        utm_medium: params.get('utm_medium') || "",
        utm_campaign: params.get('utm_campaign') || "",
        utm_content: params.get('utm_content') || "",
        utm_term: params.get('utm_term') || "",
        utm_placement: params.get('utm_placement') || "",
        audience_segment: params.get('audience_segment') || ""
    };
}

function splitFullName(fullName) {
    const parts = (fullName || "").trim().split(" ");
    const firstName = parts.shift() || "";
    const lastName = parts.join(" ") || "";
    return { firstName, lastName };
}

function sendEventToServer(payload) {
    // NOTE: Using your new endpoint name here. Update if needed.
    const endpoint = "https://server-side-capi-purchase-test.onrender.com/process-event";
    fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true // Using this excellent addition to prevent race conditions
    })
    .then(response => response.json())
    .then(data => {
        console.log(`CAPI Event [${payload.event_name}] Sent:`, data);
    })
    .catch(error => {
        console.error(`Error sending CAPI [${payload.event_name}] event:`, error);
    });
}

// ---------------------------
// 2) The Main Tracking Logic
// ---------------------------

function fireLeadEvent() {
    // Validate required fields first
    const emailValue = document.querySelector("input[name='email']")?.value || "";
    if (!emailValue.trim()) {
        console.log("Email is missing, aborting lead event.");
        return;
    }
    const termsBox = document.querySelector("input[name='terms_and_conditions']");
    if (termsBox && !termsBox.checked) {
        console.log("Terms not checked, aborting lead event.");
        return;
    }

    // Gather all data
    const fullNameValue = document.querySelector("input[name='full_name']")?.value || "";
    const phoneValue = document.querySelector("input[name='phone']")?.value || "";
    const { firstName, lastName } = splitFullName(fullNameValue);
    const utmData = extractUTMParams();

    // Construct the payload
    const leadPayload = {
        event_name: "Lead",
        event_time: Math.floor(Date.now() / 1000),
        event_source_url: window.location.href,
        action_source: "website",
        user_data: {
            email: emailValue,
            first_name: firstName,
            last_name: lastName,
            phone: phoneValue,
            fbc: getCookie('_fbc') || null,
            fbp: getCookie('_fbp') || null,
            user_agent: navigator.userAgent
        },
        custom_data: {
            ...utmData,
            currency: "SEK",
            value: 0.0
        }
    };

    // Send the data
    sendEventToServer(leadPayload);
}

// ---------------------------
// 3) Attach the Click Listener (Using the robust MutationObserver method)
// ---------------------------

function attachLeadEventListener() {
    const submitButton = document.querySelector("button.button-element[type='submit']");
    if (!submitButton) return false; // Button not found yet

    // Prevents attaching the listener multiple times if the DOM changes
    if (!submitButton.dataset.leadListenerAttached) {
        submitButton.addEventListener("click", fireLeadEvent);
        submitButton.dataset.leadListenerAttached = "true";
        console.log("CAPI Lead event listener attached to submit button.");
    }
    return true; // Button found and listener attached
}

// Wait for the DOM to be ready, then try to attach the listener.
// If the button isn't there yet (dynamic loading), use the observer to wait for it.
document.addEventListener('DOMContentLoaded', function() {
    if (!attachLeadEventListener()) {
        const observer = new MutationObserver((mutationsList, observerInstance) => {
            if (attachLeadEventListener()) {
                observerInstance.disconnect(); // Stop observing once the button is found
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }
});