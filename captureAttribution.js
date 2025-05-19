// Filename: captureAttribution.js
(function() {
    function getCookie(name) {
        let match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return match ? match[2] : null;
    }

    function captureAndStoreData() {
        const queryParams = new URLSearchParams(window.location.search);
        const attributionData = {};
        const urlParamKeysToCapture = [
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
            'utm_placement', 'audience_segment', 'fbclid', 'gclid'
        ];
        
        urlParamKeysToCapture.forEach(key => {
            if (queryParams.has(key)) {
                attributionData[key] = queryParams.get(key);
            }
        });

        attributionData._fbp = getCookie('_fbp');
        attributionData._fbc = getCookie('_fbc');
        attributionData.initialLandingPageUrl = window.location.href;
        attributionData.initialReferrer = document.referrer;
        attributionData.userAgent = navigator.userAgent;
        attributionData.captureTimestamp = Math.floor(Date.now() / 1000);

        try {
            sessionStorage.setItem('webinarOptInAttribution', JSON.stringify(attributionData));
        } catch (e) {
            // Silently fail or send to an error tracker in a more advanced setup
        }
    }

    if (document.readyState === 'complete') {
        setTimeout(captureAndStoreData, 500); 
    } else {
        window.addEventListener('load', function() {
            setTimeout(captureAndStoreData, 300);
        });
    }
})();