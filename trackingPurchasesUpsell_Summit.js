// ===================================================================
//  Meta CAPI: GHL InitiateCheckout & Save for Purchase (v5 - Production Ready)
// ===================================================================

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
function generateEventId() {
    return 'evt_' + Date.now() + '.' + Math.random().toString(36).substring(2, 9);
}
function sendEventToServer(payload) {
    const endpoint = "https://server-side-capi-purchase-test.onrender.com/process-event";
    fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true
    })
    .then(response => response.json())
    .then(data => { console.log(`CAPI Event [${payload.event_name}] Sent:`, data); })
    .catch(error => { console.error(`Error sending CAPI [${payload.event_name}] event:`, error); });
}

function handleCheckout() {
    const eventId = generateEventId();
    const orderBumpCheckbox = document.querySelector('input[name="order-bump"]');
    let purchaseValue = 297.00; // Base price
    if (orderBumpCheckbox && orderBumpCheckbox.checked) {
        purchaseValue = 394.00; // Price with bump
    }
    const emailValue = document.querySelector('input[name="email"]')?.value || "";
    if (!emailValue.trim()) { return; }
    
    const fullNameValue = document.querySelector('input[name="name"]')?.value || "";
    const phoneValue = document.querySelector('input[name="phone"]')?.value || "";
    const cityValue = document.querySelector('input[name="city"]')?.value || "";
    const zipValue = document.querySelector('input[name="zipcode"]')?.value || "";
    const { firstName, lastName } = splitFullName(fullNameValue);
    
    const purchaseData = {
        event_id: eventId,
        user_data: { email: emailValue, first_name: firstName, last_name: lastName, phone: phoneValue, city: cityValue, zip: zipValue, fbc: getCookie('_fbc') || null, fbp: getCookie('_fbp') || null, user_agent: navigator.userAgent },
        custom_data: { ...extractUTMParams(), value: purchaseValue, currency: "SEK" }
    };
    
    sessionStorage.setItem('ghl_purchase_data', JSON.stringify(purchaseData));
    console.log("Saved data to sessionStorage for Thank You page.", purchaseData);

    const initiateCheckoutPayload = {
        event_id: eventId,
        event_name: "InitiateCheckout",
        event_time: Math.floor(Date.now() / 1000),
        event_source_url: window.location.href,
        action_source: "website",
        user_data: purchaseData.user_data,
        custom_data: purchaseData.custom_data
    };
    sendEventToServer(initiateCheckoutPayload);
}

function initializeTracking() {
    function attachCheckoutListener() {
        const checkoutButton = document.querySelector('button.form-btn');
        if (!checkoutButton) return false;
        if (!checkoutButton.dataset.checkoutListenerAttached) {
            checkoutButton.addEventListener("click", handleCheckout);
            checkoutButton.dataset.checkoutListenerAttached = "true";
            console.log("CAPI Checkout event listener attached.");
        }
        return true;
    }
    if (!attachCheckoutListener()) {
        const observer = new MutationObserver(() => {
            if (attachCheckoutListener()) {
                observer.disconnect();
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }
}

// THE ROBUST SOLUTION: Check if the page is already loaded.
if (document.readyState === 'loading') {
    // Page is still loading, wait for it to be ready.
    document.addEventListener('DOMContentLoaded', initializeTracking);
} else {
    // Page is already ready, run the code now.
    initializeTracking();
}