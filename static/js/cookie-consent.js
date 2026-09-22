/*
 * Cookie consent. Google Analytics is not loaded until the visitor accepts;
 * the choice is remembered in localStorage. The measurement ID is on this
 * script tag (data-ga-id), and only production pages carry one.
 *
 * "Cookie settings" in the footer clears the choice and shows the bar again.
 */
(function () {
  "use strict";

  var KEY = "dooster-cookie-consent";
  var banner = document.getElementById("cookie-banner");
  var gaId = (document.currentScript && document.currentScript.dataset.gaId) || "";
  var loaded = false;

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

  function loadAnalytics() {
    if (loaded || !gaId) return;
    loaded = true;
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(gaId);
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", gaId);
  }

  function show() {
    if (banner) banner.hidden = false;
  }

  function hide() {
    if (banner) banner.hidden = true;
  }

  if (banner) {
    banner.addEventListener("click", function (e) {
      var choice = e.target.closest("[data-cookie-choice]");
      if (!choice) return;
      remember(choice.dataset.cookieChoice);
      hide();
      if (choice.dataset.cookieChoice === "accepted") loadAnalytics();
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
  if (choice === "accepted") {
    loadAnalytics();
  } else if (choice !== "declined") {
    show();
  }
})();
