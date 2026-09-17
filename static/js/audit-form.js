/*
 * Free AI visibility check — posts the website and email to the audit
 * endpoint (AUDIT_ENDPOINT at build time). The Lambda starts the audit in the
 * background and emails the report, so this only waits for "accepted".
 */
(function () {
  "use strict";

  var form = document.getElementById("audit-form");
  if (!form) return;

  var statusEl = document.getElementById("audit-status");
  var submitBtn = document.getElementById("audit-submit");
  var endpoint = form.dataset.endpoint;
  var email = form.dataset.email;
  var submitLabel = submitBtn ? submitBtn.textContent : "Run";
  var EMAIL_RE = /^[^@\s]+@[^@\s.]+\.[^@\s]+$/;
  var URL_RE = /^(https?:\/\/)?([a-z0-9-]+\.)+[a-z]{2,}(:\d+)?(\/\S*)?$/i;

  var MSG = {};
  try {
    MSG = JSON.parse(document.getElementById("audit-messages").textContent);
  } catch (e) {
    MSG = {};
  }

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

  function collect() {
    var data = {};
    new FormData(form).forEach(function (value, key) {
      data[key] = typeof value === "string" ? value.trim() : value;
    });
    data.consent = form.querySelector('[name="consent"]').checked;
    return data;
  }

  function validate(data) {
    var errors = {};
    if (!data.url) errors.url = "Please enter your website address.";
    else if (!URL_RE.test(data.url)) errors.url = "That doesn't look like a website address.";
    if (!data.email) errors.email = "Please enter your email address.";
    else if (!EMAIL_RE.test(data.email)) errors.email = "That doesn't look like a valid email address.";
    return errors;
  }

  function showStatus(kind, heading, bodyHtml) {
    statusEl.innerHTML =
      '<div class="form-msg form-msg-' + kind + '">' +
      (heading ? "<strong>" + heading + "</strong>" : "") + (bodyHtml || "") + "</div>";
  }

  function setBusy(busy) {
    submitBtn.disabled = busy;
    submitBtn.style.opacity = busy ? "0.6" : "";
    submitBtn.textContent = busy ? MSG.sending || "Starting…" : submitLabel;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    ["url", "email", "first_name", "company"].forEach(function (n) { setError(n, ""); });
    var data = collect();
    var errors = validate(data);
    if (Object.keys(errors).length) {
      Object.keys(errors).forEach(function (k) { setError(k, errors[k]); });
      showStatus("error", null, MSG.validation_summary);
      var first = form.querySelector('[aria-invalid="true"]');
      if (first) first.focus();
      return;
    }
    if (!endpoint) {
      showStatus("error", null, MSG.not_connected + mailLink());
      return;
    }

    setBusy(true);
    statusEl.innerHTML = "";
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
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
          return;
        }
        if (result.body.errors) {
          Object.keys(result.body.errors).forEach(function (k) { setError(k, result.body.errors[k]); });
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

  form.addEventListener("input", function (e) {
    if (e.target.name) setError(e.target.name, "");
  });
})();
