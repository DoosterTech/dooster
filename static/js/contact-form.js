/*
 * Contact form — posts JSON to the API Gateway endpoint set in the form's
 * data-endpoint attribute (FORM_ENDPOINT at build time).
 *
 * Validates in the browser for fast feedback, but the Lambda validates again;
 * client-side checks are a convenience, never the guard.
 */
(function () {
  "use strict";

  var form = document.getElementById("contact-form");
  if (!form) return;

  var statusEl = document.getElementById("form-status");
  var submitBtn = document.getElementById("contact-submit");
  var endpoint = form.dataset.endpoint;
  var email = form.dataset.email;

  var MSG = {};
  try {
    MSG = JSON.parse(document.getElementById("form-messages").textContent);
  } catch (e) {
    MSG = {};
  }

  var submitLabel = submitBtn ? submitBtn.textContent : "Send";
  var EMAIL_RE = /^[^@\s]+@[^@\s.]+\.[^@\s]+$/;
  var FIELDS = ["first_name", "last_name", "email", "company", "site_url", "message"];

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function mailLink() {
    return ' <a href="mailto:' + email + '">' + email + "</a>";
  }

  function setError(name, message) {
    var input = form.querySelector('[name="' + name + '"]');
    var slot = document.getElementById("err-" + name);
    if (slot) slot.textContent = message || "";
    if (input) {
      input.setAttribute("aria-invalid", message ? "true" : "false");
      input.classList.toggle("has-error", !!message);
    }
  }

  function clearErrors() {
    FIELDS.forEach(function (n) { setError(n, ""); });
  }

  function collect() {
    var data = {};
    new FormData(form).forEach(function (value, key) {
      data[key] = typeof value === "string" ? value.trim() : value;
    });
    return data;
  }

  function validate() {
    var errors = {};
    var data = collect();
    if (!data.first_name) errors.first_name = "Please enter your first name.";
    if (!data.email) {
      errors.email = "Please enter your email address.";
    } else if (!EMAIL_RE.test(data.email)) {
      errors.email = "That doesn't look like a valid email address.";
    }
    if (!data.message) errors.message = "Please tell us a little about your project.";
    return errors;
  }

  // heading and body are trusted strings from the page; server text is escaped
  function showStatus(kind, heading, bodyHtml) {
    if (!statusEl) return;
    statusEl.innerHTML =
      '<div class="form-msg form-msg-' + kind + '">' +
      (heading ? "<strong>" + heading + "</strong>" : "") +
      (bodyHtml || "") +
      "</div>";
  }

  function setBusy(busy) {
    if (!submitBtn) return;
    submitBtn.disabled = busy;
    submitBtn.style.opacity = busy ? "0.6" : "";
    submitBtn.style.cursor = busy ? "wait" : "";
    submitBtn.textContent = busy ? MSG.sending || "Sending…" : submitLabel;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearErrors();

    var errors = validate();
    if (Object.keys(errors).length) {
      Object.keys(errors).forEach(function (k) { setError(k, errors[k]); });
      showStatus("error", null, MSG.validation_summary);
      var first = form.querySelector('[aria-invalid="true"]');
      if (first) first.focus();
      return;
    }

    if (!endpoint) {
      // No endpoint configured — say so plainly rather than failing silently.
      showStatus("error", null, MSG.not_connected + mailLink());
      return;
    }

    setBusy(true);
    statusEl.innerHTML = "";

    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collect()),
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          return { ok: res.ok, body: body };
        });
      })
      .then(function (result) {
        setBusy(false);
        if (result.ok && result.body.ok) {
          form.reset();
          showStatus("success", MSG.success_heading, MSG.success_body);
          statusEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
          return;
        }
        if (result.body.errors) {
          Object.keys(result.body.errors).forEach(function (k) {
            setError(k, result.body.errors[k]);
          });
          showStatus("error", null, MSG.validation_summary);
          return;
        }
        showStatus("error", null,
          (result.body.error ? escapeHtml(result.body.error) : MSG.error) + mailLink());
      })
      .catch(function () {
        setBusy(false);
        showStatus("error", null, MSG.error + mailLink());
      });
  });

  // Clear a field's error as soon as the user starts fixing it
  form.addEventListener("input", function (e) {
    if (e.target.name) setError(e.target.name, "");
  });
})();
