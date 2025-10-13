// ===================================================================
//  Meta CAPI Event Tracking: GHL Buyer Thank You Page (v1)
//  Fires Purchase event and submits hidden form to sync event_id.
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
        
        // --- Part 1: Submit the invisible form to sync the event_id to the contact record ---
        // NOTE: These selectors target standard GHL form fields. Verify them if issues arise.
        const hiddenFormEmail = document.querySelector('form input[name="email"]');
        const hiddenFormEventId = document.querySelector('form input[name="capi_event_id"]');
        const hiddenFormSubmitButton = document.querySelector('form button[type="submit"]');

        if (hiddenFormEmail && hiddenFormEventId && hiddenFormSubmitButton) {
            hiddenFormEmail.value = savedData.user_data.email;
            hiddenFormEventId.value = savedData.event_id;
            
            console.log(`Populating invisible form with Email and Event ID (${savedData.event_id}) and submitting...`);
            hiddenFormSubmitButton.click(); // This updates the contact in GHL
        } else {
            console.warn("Could not find all elements of the hidden CAPI form on the Thank You page.");
        }

        // --- Part 2: Fire the client-side Purchase event ---
        const purchasePayload = {
            event_id: savedData.event_id, // The crucial matching ID
            event_name: "Purchase",
            event_time: Math.floor(Date.now() / 1000),
            event_source_url: window.location.href,
            action_source: "website",
            user_data: savedData.user_data,
            custom_data: savedData.custom_data
        };
        
        sendEventToServer(purchasePayload);
        
        // --- Part 3: Clean up ---
        sessionStorage.removeItem('ghl_purchase_data');
        console.log("Cleared purchase data from sessionStorage.");

    } else {
        console.log("No purchase data found in sessionStorage on Thank You page.");
    }
});