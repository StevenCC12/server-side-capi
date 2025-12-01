/**
 * Meta CAPI Event Tracking: GHL Lead Event
 * Production Version (Passive Observer / Event Delegation)
 */

(function() { // Wrapped in IIFE to protect global scope

  // 1. Helper Functions
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
      const endpoint = "https://knaa-server-side-capi.onrender.com";
      
      fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true // Critical for survival during redirect
      }).catch(function(e) {
          // Fail silently in production
      });
  }

  // 2. Main Logic
  function fireLeadEvent() {
      // Selectors
      const emailInput = document.querySelector("input[name='email']");
      const termsBox = document.querySelector("input[name='terms_and_conditions']");
      const nameInput = document.querySelector("input[name='full_name']");
      const phoneInput = document.querySelector("input[name='phone']");
      
      const emailValue = emailInput ? emailInput.value : "";
      const fullNameValue = nameInput ? nameInput.value : "";
      const phoneValue = phoneInput ? phoneInput.value : "";

      // Basic local validation to avoid firing on empty clicks
      // We let GHL handle the UI error messages
      if (!emailValue.trim()) return;
      if (termsBox && !termsBox.checked) return;

      const { firstName, lastName } = splitFullName(fullNameValue);
      const utmData = extractUTMParams();

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
      
      sendEventToServer(leadPayload);
  }

  // 3. Global Event Delegation
  document.addEventListener("click", function(e) {
      // Check if the clicked element (or its parent) is the submit button
      const targetButton = e.target.closest("button.button-element[type='submit']");
      
      if (targetButton) {
          // Fire CAPI logic passively; do not preventDefault
          fireLeadEvent();
      }
  }, true); // Capture phase

})();