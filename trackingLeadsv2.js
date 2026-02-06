/**
 * Meta CAPI Event Tracking: GHL Lead Event
 * Production Version (With Deduplication & Event ID)
 */

(function() { // Wrapped in IIFE to protect global scope

  // --- 1. Helper Functions ---
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
      // REPLACE WITH YOUR PRODUCTION RENDER URL
      const endpoint = "https://your-app-name.onrender.com/process-event"; 
      
      // For local testing, you can swap back to:
      // const endpoint = "https://barratrous-uncontestablely-annie.ngrok-free.dev/process-event";

      fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true // Critical for survival during redirect
      }).catch(function(e) {
          console.error("CAPI Send Failed", e);
      });
  }

  // --- 2. Main Logic ---
  function fireLeadEvent() {
      // Selectors (Verify these match your GHL form exactly)
      const emailInput = document.querySelector("input[name='email']");
      const termsBox = document.querySelector("input[name='terms_and_conditions']");
      const nameInput = document.querySelector("input[name='full_name']");
      const phoneInput = document.querySelector("input[name='phone']");
      
      const emailValue = emailInput ? emailInput.value : "";
      const fullNameValue = nameInput ? nameInput.value : "";
      const phoneValue = phoneInput ? phoneInput.value : "";

      // Basic Validation
      if (!emailValue.trim()) return;
      if (termsBox && !termsBox.checked) return;

      const { firstName, lastName } = splitFullName(fullNameValue);
      const utmData = extractUTMParams();

      // --- DEDUPLICATION LOGIC STARTS HERE ---
      
      // 1. Generate a Unique Event ID
      const eventId = 'lead_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);

      // 2. Fire Browser Pixel (Client-Side) with the ID
      if (typeof fbq === 'function') {
          fbq('track', 'Lead', {
              content_name: 'Webinar Opt-in',
              currency: 'SEK',
              value: 0.0
          }, { eventID: eventId }); // <--- The Bridge
      }

      // 3. Build Server Payload (Server-Side) with the ID
      const leadPayload = {
          event_id: eventId, // <--- The Bridge
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
      
      // 4. Send to Server
      sendEventToServer(leadPayload);
  }

  // --- 3. Global Event Delegation ---
  document.addEventListener("click", function(e) {
      // Check if the clicked element (or its parent) is the submit button
      const targetButton = e.target.closest("button.button-element[type='submit']");
      
      if (targetButton) {
          // Fire Logic Passively
          fireLeadEvent();
      }
  }, true); // Capture phase

})();