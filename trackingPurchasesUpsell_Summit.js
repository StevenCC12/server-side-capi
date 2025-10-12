// ===================================================================
//  Meta CAPI Event Tracking: GHL InitiateCheckout & Save for Purchase (v2)
//  Handles variable pricing from order bumps and uses specific GHL selectors.
// ===================================================================

// ---------------------------
// 1) Helper Functions (Reusable)
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

// Added this function back in to handle the 'name' field
function splitFullName(fullName) {
    const parts = (fullName || "").trim().split(" ");
    const firstName = parts.shift() || "";
    const lastName = parts.join(" ") || "";
    return { firstName, lastName };
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
    .then(data => {
        console.log(`CAPI Event [${payload.event_name}] Sent:`, data);
    })
    .catch(error => {
        console.error(`Error sending CAPI [${payload.event_name}] event:`, error);
    });
}

// ---------------------------
// 2) The Main Checkout Logic
// ---------------------------

function handleCheckout() {
    console.log("Checkout button clicked, capturing data.");
    
    // --- Step 1: Determine the price based on the order bump ---
    const orderBumpCheckbox = document.querySelector('input[name="order-bump"]');
    let purchaseValue = 297.00; // Base price of the upsell
    
    if (orderBumpCheckbox && orderBumpCheckbox.checked) {
        purchaseValue = 394.00; // Price with the order bump
        console.log("Order bump is checked. Total value:", purchaseValue);
    } else {
        console.log("Order bump is NOT checked. Total value:", purchaseValue);
    }

    // --- Step 2: Gather all user data from the form using your new selectors ---
    const fullNameValue = document.querySelector('input[name="name"]')?.value || "";
    const emailValue = document.querySelector('input[name="email"]')?.value || "";
    const phoneValue = document.querySelector('input[name="phone"]')?.value || "";
    const cityValue = document.querySelector('input[name="city"]')?.value || "";
    const zipValue = document.querySelector('input[name="zipcode"]')?.value || "";
    
    const { firstName, lastName } = splitFullName(fullNameValue);
    
    if (!emailValue.trim()) {
        console.warn("Email field is empty. Aborting tracking.");
        return;
    }
    
    const utmData = extractUTMParams();

    // --- Step 3: Save ALL data for the Thank You page ---
    const purchaseData = {
        user_data: {
            email: emailValue,
            first_name: firstName,
            last_name: lastName,
            phone: phoneValue,
            city: cityValue,
            zip: zipValue,
            fbc: getCookie('_fbc') || null,
            fbp: getCookie('_fbp') || null,
            user_agent: navigator.userAgent
        },
        custom_data: {
            ...utmData,
            value: purchaseValue, // Save the final calculated value
            currency: "SEK"
        }
    };
    sessionStorage.setItem('ghl_purchase_data', JSON.stringify(purchaseData));
    console.log("Saved data to sessionStorage for Thank You page.", purchaseData);

    // --- Step 4: Fire the InitiateCheckout event now ---
    const initiateCheckoutPayload = {
        event_name: "InitiateCheckout",
        event_time: Math.floor(Date.now() / 1000),
        event_source_url: window.location.href,
        action_source: "website",
        user_data: purchaseData.user_data, // Reuse the data
        custom_data: purchaseData.custom_data // Reuse the data
    };

    sendEventToServer(initiateCheckoutPayload);
}

// ---------------------------
// 3) Attach the Click Listener
// ---------------------------

function attachCheckoutListener() {
    // Using the specific selector for your button
    const checkoutButton = document.querySelector('button.form-btn');
    
    if (!checkoutButton) return false;

    if (!checkoutButton.dataset.checkoutListenerAttached) {
        checkoutButton.addEventListener("click", handleCheckout);
        checkoutButton.dataset.checkoutListenerAttached = "true";
        console.log("CAPI Checkout event listener attached to purchase button.");
    }
    return true;
}

document.addEventListener('DOMContentLoaded', function() {
    if (!attachCheckoutListener()) {
        const observer = new MutationObserver((mutationsList, observerInstance) => {
            if (attachCheckoutListener()) {
                observerInstance.disconnect();
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }
});