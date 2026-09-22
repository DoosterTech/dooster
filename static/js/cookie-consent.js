/*
 * Cookie consent with Google Consent Mode.
 *
 * By default every storage type is denied: Google Analytics still counts page
 * views, but sends cookieless pings and stores nothing on the device, so no
 * consent is needed for a basic visitor count. Accepting upgrades
 * analytics_storage to granted, which turns on cookies and the fuller
 * reporting (returning visitors, sessions, Tag Manager and so on).
 *
 * The measurement ID is on this script tag (data-ga-id); only production pages
 * carry one. The choice is remembered in localStorage.
 *
 * "Cookie settings" in the footer clears the choice and shows the bar again.
 */
(function () {
  "use strict";

  var KEY = "dooster-cookie-consent";
  var banner = document.getElementById("cookie-banner");
  var gaId = (document.currentScript && document.currentScript.dataset.gaId) || "";

  function stored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null;   // private mode or blocked storage: treat as undecided
    }
  }

  function remember(choice) {
    try {
      localStorage.setItem(KEY, choice);
    } catch (e) { /* nothing we can do; the bar will simply ask again */ }
  }

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;

  function startAnalytics(accepted) {
    if (!gaId) return;
    // Consent defaults must be set before the tag loads
    gtag("consent", "default", {
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      analytics_storage: accepted ? "granted" : "denied",
    });
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(gaId);
    document.head.appendChild(s);
    gtag("js", new Date());
    gtag("config", gaId);
  }

  function grant() {
    if (!gaId) return;
    gtag("consent", "update", { analytics_storage: "granted" });
  }

  function show() { if (banner) banner.hidden = false; }
  function hide() { if (banner) banner.hidden = true; }

  if (banner) {
    banner.addEventListener("click", function (e) {
      var choice = e.target.closest("[data-cookie-choice]");
      if (!choice) return;
      remember(choice.dataset.cookieChoice);
      hide();
      if (choice.dataset.cookieChoice === "accepted") grant();
    });
  }

  // Footer link: forget the choice and ask again
  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-cookie-settings]")) return;
    e.preventDefault();
    try { localStorage.removeItem(KEY); } catch (err) { /* ignore */ }
    show();
  });

  var choice = stored();
  startAnalytics(choice === "accepted");
  if (!choice) show();
})();
