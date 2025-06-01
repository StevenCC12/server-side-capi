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
  let utmCampaign = "";
  let utmContent = "";
  let utmTerm = "";
  let utmPlacement = "";
  let audienceSegment = "";

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
      case "utm_campaign":
        utmCampaign = paramValue;
        break;
      case "utm_content":
        utmContent = paramValue;
        break;
      case "utm_term":
        utmTerm = paramValue;
        break;
      case "utm_placement":
        utmPlacement = paramValue;
        break;
      case "audience_segment":
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
// ---------------------------
function getFBCookie() {
  return getCookie('_fbc'); 
}

// ---------------------------
// 3) Get _fbp from cookies
// ---------------------------
function getFBPookie() { 
  return getCookie('_fbp');
}

// ---------------------------
// 4) Split full name into first & last
// ---------------------------
function splitFullName(fullName) {
  let parts = (fullName || "").trim().split(" ");
  let firstName = parts.shift() || "";
  let lastName = parts.join(" ") || "";
  return { firstName, lastName };
}

// ---------------------------
// 5) Fire Lead Event (UPDATED with Qualifying Question - VARIANT 3)
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
  let fbcValue = getFBCookie() || ""; 
  let fbpValue = getFBPookie() || ""; 
  
  let userAgentValue = navigator.userAgent;

  // Check if the Terms & Conditions checkbox is present and checked
  let termsBox = document.querySelector("input[name='terms_and_conditions']");
  if (termsBox && !termsBox.checked) {
    // console.log("Terms not checked, aborting lead event.");
    return; 
  }

  // Validate required fields
  if (!emailValue.trim()) { 
    // console.log("Email is missing, aborting lead event.");
    return; 
  }

  // ---------------------------
  // NEW: Check for Qualifying Radio Button (FOR THIRD OPT-IN PAGE)
  // ---------------------------
  // ID for this page's radio button: "Ja_Ky0o8Fkr07KfJJwakDLJ_0_8iudxrwpd9"
  const qualifyingRadioButton = document.querySelector("#Ja_Ky0o8Fkr07KfJJwakDLJ_0_8iudxrwpd9");

  if (!qualifyingRadioButton || !qualifyingRadioButton.checked) {
    // console.log("Qualifying radio button 'Ja' is not selected, aborting lead event.");
    return; 
  }

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
        fbc: fbcValue,
        fbp: fbpValue,
        user_agent: userAgentValue
      },
      custom_data: {
        utm_source: utmSource,
        utm_medium: utmMedium,
        utm_campaign: utmCampaign,
        utm_content: utmContent,
        utm_term: utmTerm,
        utm_placement: utmPlacement,
        audience_segment: audienceSegment,
        currency: "SEK",
        value: 0.0
      }
    })
  })
  .then(response => {
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
      fireLeadEvent();
    });
    submitButton.dataset.leadListenerAttached = "true";
  }
  return true;
}

// Use a more robust way to wait for the DOM and then attach the listener
if (document.readyState === 'loading') { 
  document.addEventListener('DOMContentLoaded', function() {
    if (!attachLeadEventListener()) {
      const observer = new MutationObserver((mutationsList, observerInstance) => {
        if (attachLeadEventListener()) {
          observerInstance.disconnect();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
  });
} else { 
  if (!attachLeadEventListener()) {
    const observer = new MutationObserver((mutationsList, observerInstance) => {
      if (attachLeadEventListener()) {
        observerInstance.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
}