(async function() { // IIFE is async for await

    function toTitleCase(str) {
      if (!str || typeof str !== 'string') return '';
      return str.toLowerCase().replace(/\b(\w)/g, s => s.toUpperCase());
    }

    const queryParams = new URLSearchParams(window.location.search);

    const wjEmail = queryParams.get('wj_lead_email');
    const wjFirstNameInput = queryParams.get('wj_lead_first_name') || '';
    const wjLastNameInput = queryParams.get('wj_lead_last_name') || '';
    const wjPhoneCountryCode = queryParams.get('wj_lead_phone_country_code');
    const wjPhoneNumber = queryParams.get('wj_lead_phone_number');
    const wjLiveRoomLink = queryParams.get('wj_lead_unique_link_live_room');
    const wjEventTimestamp = queryParams.get('wj_event_ts');

    let finalFirstName = '', finalLastName = '';
    if (wjLastNameInput.trim() !== '') {
        finalFirstName = wjFirstNameInput.trim();
        finalLastName = wjLastNameInput.trim();
    } else if (wjFirstNameInput.trim() !== '') {
        const nameParts = wjFirstNameInput.trim().split(/\s+/);
        finalFirstName = nameParts[0] || '';
        if (nameParts.length > 1) finalLastName = nameParts.slice(1).join(' ').trim();
    }
    
    let fullPhoneNumber = '';
    if (wjPhoneCountryCode && wjPhoneNumber) {
        fullPhoneNumber = wjPhoneCountryCode.trim() + wjPhoneNumber.trim();
    } else if (wjPhoneNumber) {
        fullPhoneNumber = wjPhoneNumber.trim();
    }

    let derivedCountryISO = '';
    if (wjPhoneCountryCode) {
        const countryCodeToISOMap = {
            "+43": "AT", "+32": "BE", "+1": "US", "+41": "CH", "+420": "CZ",
            "+49": "DE", "+45": "DK", "+34": "ES", "+358": "FI", "+33": "FR",
            "+44": "GB", "+30": "GR", "+36": "HU", "+353": "IE", "+39": "IT",
            "+52": "MX", "+31": "NL", "+47": "NO", "+48": "PL", "+351": "PT",
            "+40": "RO", "+421": "SK", "+46": "SE"
        };
        derivedCountryISO = countryCodeToISOMap[wjPhoneCountryCode.trim()] || '';
    }

    let initialAttribution = {};
    try {
        const storedAttribution = sessionStorage.getItem('webinarOptInAttribution');
        if (storedAttribution) {
            initialAttribution = JSON.parse(storedAttribution);
        }
    } catch (e) { /* Silently fail in production for sessionStorage retrieval */ }

    function getCurrentCookie(name) {
        let match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return match ? match[2] : null;
    }

    if (wjEmail) {
        const payloadToSend = {
            email: wjEmail,
            firstName: toTitleCase(finalFirstName),
            lastName: toTitleCase(finalLastName),
            phone: fullPhoneNumber || null,
            country: derivedCountryISO || null,
            tags: ['webinar-registered-prod-attr', 'source-webinarjam-final'], // Example production tags
            source: initialAttribution.utm_source ? 
                    `Webinar (${initialAttribution.utm_source})` : 
                    'WebinarJam Registration',
            utm_source: initialAttribution.utm_source || null,
            utm_medium: initialAttribution.utm_medium || null,
            utm_campaign: initialAttribution.utm_campaign || null,
            utm_content: initialAttribution.utm_content || null,
            utm_term: initialAttribution.utm_term || null,
            utm_placement: initialAttribution.utm_placement || null,
            audience_segment: initialAttribution.audience_segment || null,
            fbclid: initialAttribution.fbclid || null,
            gclid: initialAttribution.gclid || null, 
            fbc_cookie: initialAttribution._fbc || getCurrentCookie('_fbc') || null,
            fbp_cookie: initialAttribution._fbp || getCurrentCookie('_fbp') || null,
            initial_landing_page_url: initialAttribution.initialLandingPageUrl || null,
            initial_referrer_url: initialAttribution.initialReferrer || null,
            user_agent: initialAttribution.userAgent || navigator.userAgent,
            wj_lead_unique_link_live_room: wjLiveRoomLink || null,
            wj_event_ts: wjEventTimestamp || null,
        };

        const finalPayload = {};
        for (const key in payloadToSend) {
            if (payloadToSend[key] !== null && payloadToSend[key] !== undefined) {
                 if ((key === 'firstName' || key === 'lastName') && payloadToSend[key] === '') {
                     finalPayload[key] = ''; 
                } else if (payloadToSend[key] !== '') {
                    finalPayload[key] = payloadToSend[key];
                }
            }
        }
        
        const psfWebhookUrl = 'https://services.leadconnectorhq.com/hooks/kFKnF888dp7eKChjLxb9/webhook-trigger/b5d16c5e-d4bf-4c90-8351-858c43386599'; 
        
        const MAX_ATTEMPTS = 3;
        const RETRY_DELAY_MS = 2000;

        async function sendWebhookWithRetries() {
            for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
                try {
                    const response = await fetch(psfWebhookUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(finalPayload),
                    });
                    if (response.ok) return true; 
                    if (attempt === MAX_ATTEMPTS) return false; 
                } catch (error) { 
                    if (attempt === MAX_ATTEMPTS) return false;
                }
                if (attempt < MAX_ATTEMPTS) {
                    await new Promise(resolve => setTimeout(resolve, RETRY_DELAY_MS));
                }
            }
            return false;
        }
        await sendWebhookWithRetries();
    }
})();