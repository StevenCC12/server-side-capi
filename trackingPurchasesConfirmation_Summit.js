// ===================================================================
//  Meta CAPI: GHL Buyer Thank You Page (v2 - Production)
// ===================================================================

function sendEventToServer(payload) {
    const endpoint = "https://server-side-capi-purchase-test.onrender.com/process-event";
    fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true
    })
    .then(response => response.json())
    .then(data => console.log(`CAPI Event [${payload.event_name}] Sent:`, data))
    .catch(error => console.error(`Error sending CAPI [${payload.event_name}] event:`, error));
}

document.addEventListener('DOMContentLoaded', function() {
    const savedDataString = sessionStorage.getItem('ghl_purchase_data');

    if (savedDataString) {
        console.log("Found purchase data in sessionStorage.");
        const savedData = JSON.parse(savedDataString);
        
        const hiddenFormEmail = document.querySelector('form input[name="email"]');
        const hiddenFormEventId = document.querySelector('form input[name="capi_event_id"]');
        const hiddenFormSubmitButton = document.querySelector('form button[type="submit"]');

        if (hiddenFormEmail && hiddenFormEventId && hiddenFormSubmitButton) {
            hiddenFormEmail.value = savedData.user_data.email;
            hiddenFormEventId.value = savedData.event_id;
            console.log(`Populating invisible form with Event ID (${savedData.event_id}) and submitting...`);
            hiddenFormSubmitButton.click();
        } else {
            console.warn("Could not find hidden CAPI form on Thank You page.");
        }

        const purchasePayload = {
            event_id: savedData.event_id,
            event_name: "Purchase",
            event_time: Math.floor(Date.now() / 1000),
            event_source_url: window.location.href,
            action_source: "website",
            user_data: savedData.user_data,
            custom_data: savedData.custom_data
        };
        
        sendEventToServer(purchasePayload);
        
        sessionStorage.removeItem('ghl_purchase_data');
    } else {
        console.log("No purchase data found in sessionStorage on Thank You page.");
    }
});