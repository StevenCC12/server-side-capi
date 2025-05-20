// ---------------------------
// 0) Get Cookie for Consent (and other cookies)
// ---------------------------
function getCookie(name) {
  let match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? match[2] : null;
}

// ---------------------------
// 1) Extract UTM from URL (UPDATED)
// ---------------------------
function extractUTMParams() {
  let utmSource = "";
  let utmMedium = "";
  let utmCampaign = ""; // Added
  let utmContent = "";
  let utmTerm = "";    // Added
  let utmPlacement = ""; // Added
  let audienceSegment = ""; // Added

  const queryString = window.location.search.substring(1);
  const re = /([^&=]+)=([^&]*)/g;
  let match;

  while ((match = re.exec(queryString))) {
    const paramName = decodeURIComponent(match[1]);
    const paramValue = decodeURIComponent(match[2]);
    switch (paramName) {
      case "utm_source":
        utmSource = paramValue;
        break;
      case "utm_medium":
        utmMedium = paramValue;
        break;
      case "utm_campaign": // Added
        utmCampaign = paramValue;
        break;
      case "utm_content":
        utmContent = paramValue;
        break;
      case "utm_term":    // Added
        utmTerm = paramValue;
        break;
      case "utm_placement": // Added
        utmPlacement = paramValue;
        break;
      case "audience_segment": // Added
        audienceSegment = paramValue;
        break;
    }
  }
  return { 
    utmSource, 
    utmMedium, 
    utmCampaign, 
    utmContent, 
    utmTerm, 
    utmPlacement, 
    audienceSegment 
  };
}

// ---------------------------
// 2) Get _fbc from cookies 
// (Note: Meta prefers the full cookie string for fbc, not just the click ID part. 
// Your previous script was splitting it, this one will too, ensure your CAPI endpoint handles it or expects full cookie string)
// For fbc, Meta expects the full fbc parameter value, which includes the prefix “fb”, the subdomain index, the creation time, and the fbclid.
// Example: fb.1.1554739892709.AbCdEfGhIjKlMnOpQrStUvWxYz
// ---------------------------
function getFBCookie() { // Renamed for clarity, returns full _fbc cookie if found
  return getCookie('_fbc'); 
}

// ---------------------------
// 3) Get _fbp from cookies
// ---------------------------
function getFBPookie() { // Renamed for clarity, returns full _fbp cookie if found
  return getCookie('_fbp');
}

// ---------------------------
// 4) Split full name into first & last
// ---------------------------
function splitFullName(fullName) {
  let parts = (fullName || "").trim().split(" "); // Ensure fullName is a string
  let firstName = parts.shift() || "";
  let lastName = parts.join(" ") || "";
  return { firstName, lastName };
}

// ---------------------------
// 5) Fire Lead Event (UPDATED custom_data)
// ---------------------------
function fireLeadEvent() {
  // Extract UTM parameters from the URL
  let { 
    utmSource, 
    utmMedium, 
    utmCampaign,
    utmContent, 
    utmTerm,
    utmPlacement,
    audienceSegment
  } = extractUTMParams();

  // Gather form data
  let fullNameValue = document.querySelector("input[name='full_name']")?.value || "";
  let emailValue = document.querySelector("input[name='email']")?.value || "";
  let phoneValue = document.querySelector("input[name='phone']")?.value || "";

  let { firstName, lastName } = splitFullName(fullNameValue);
  
  // Get full fbc and fbp cookie values
  let fbcValue = getFBCookie() || ""; // Default to empty string if null
  let fbpValue = getFBPookie() || ""; // Default to empty string if null
  
  let userAgentValue = navigator.userAgent;

  // Check if the Terms & Conditions checkbox is present and checked
  let termsBox = document.querySelector("input[name='terms_and_conditions']");
  if (termsBox && !termsBox.checked) {
    // console.log("Terms not checked, aborting lead event.");
    return; 
  }

  // Validate required fields (adjust as per your form's actual requirements)
  // Assuming email is the primary required field for a lead event
  if (!emailValue.trim()) { 
    // console.log("Email is missing, aborting lead event.");
    return; 
  }
  // You might want to make firstName also required if your CAPI relies on it heavily
  // if (!firstName.trim()) {
  //   console.log("First name is missing (after splitting full name), aborting lead event.");
  //   return;
  // }


  // Fire the server-side event
  fetch("https://knaa-server-side-capi.onrender.com/process-event/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_name: "Lead",
      event_time: Math.floor(Date.now() / 1000),
      event_source_url: window.location.href,
      action_source: "website",
      user_data: {
        email: emailValue,
        first_name: firstName,
        last_name: lastName,
        phone: phoneValue,
        fbc: fbcValue,    // Send the full _fbc cookie value
        fbp: fbpValue,    // Send the full _fbp cookie value
        user_agent: userAgentValue
        // fbclid can also be sent in user_data if captured, 
        // but fbc is generally preferred if available
        // "fbclid": extractedFbclidValue (if you extract it separately in extractUTMParams) 
      },
      custom_data: { // UPDATED THIS SECTION
        utm_source: utmSource,
        utm_medium: utmMedium,       // This will be 'paid' from your FB Ad setup
        utm_campaign: utmCampaign,   // This will be FB Campaign ID
        utm_content: utmContent,     // This will be FB Ad ID
        utm_term: utmTerm,           // This will be FB Ad Set ID
        utm_placement: utmPlacement,
        audience_segment: audienceSegment,
        currency: "SEK",
        value: 0.0
      }
    })
  })
  .then(response => {
    // Optional: Basic response handling, even if silent in production
    // if (!response.ok) {
    //   console.error("CAPI event send failed with status:", response.status);
    // } else {
    //   console.log("CAPI event potentially sent successfully.");
    // }
  })
  .catch(error => {
    // console.error("Error sending CAPI event:", error);
  });
}

// ---------------------------
// 6) Attach the Click Listener with MutationObserver
// ---------------------------
function attachLeadEventListener() {
  const submitButton = document.querySelector("button[type='submit']");
  if (!submitButton) return false;
  if (!submitButton.dataset.leadListenerAttached) {
    submitButton.addEventListener("click", function() {
      // Optional: Add a small delay to allow form validation or other scripts to run
      // setTimeout(fireLeadEvent, 100); 
      fireLeadEvent();
    });
    submitButton.dataset.leadListenerAttached = "true";
  }
  return true;
}

// Use a more robust way to wait for the DOM and then attach the listener
if (document.readyState === 'loading') { // Loading hasn't finished yet
  document.addEventListener('DOMContentLoaded', function() {
    // Try to attach, and use observer if element not yet present (for dynamic forms)
    if (!attachLeadEventListener()) {
      const observer = new MutationObserver((mutationsList, observerInstance) => {
        if (attachLeadEventListener()) {
          observerInstance.disconnect();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
  });
} else { // DOMContentLoaded has already fired
  // Try to attach, and use observer if element not yet present
  if (!attachLeadEventListener()) {
    const observer = new MutationObserver((mutationsList, observerInstance) => {
      if (attachLeadEventListener()) {
        observerInstance.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
}