/* Site-wide behaviour: mobile menu and dropdown aria state. */
(function () {
  "use strict";

  var toggle = document.querySelector(".menu-toggle");
  var menu = document.getElementById("mobile-menu");

  function setOpen(open) {
    if (!toggle || !menu) return;
    menu.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    document.body.style.overflow = open ? "hidden" : "";
  }

  if (toggle && menu) {
    toggle.addEventListener("click", function () { setOpen(menu.hidden); });
    // an in-page link (e.g. /#why-dooster) should close the menu it was tapped in
    menu.addEventListener("click", function (e) { if (e.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    window.addEventListener("resize", function () { if (window.innerWidth >= 1024) setOpen(false); });
  }

  document.querySelectorAll(".nav-group").forEach(function (group) {
    var parent = group.querySelector(".nav-link");
    var set = function (v) { parent.setAttribute("aria-expanded", v); };
    group.addEventListener("mouseenter", function () { set("true"); });
    group.addEventListener("mouseleave", function () { set("false"); });
    group.addEventListener("focusin", function () { set("true"); });
    group.addEventListener("focusout", function (e) { if (!group.contains(e.relatedTarget)) set("false"); });
  });
})();
