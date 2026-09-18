"""
Free AI visibility check: API Gateway (HTTP API) -> Lambda -> SES.

One function, two modes:

  1. HTTP request from the website form. Validates the website and email,
     then invokes this same function asynchronously and returns 202 straight
     away, because an audit takes longer than API Gateway's 30-second limit.
  2. Async job ({"dooster_job": {...}}). Runs aeo_audit.run_audit(), emails the
     visitor a summary with the full HTML report attached, and notifies the
     Dooster inbox about the lead.

aeo_audit.py is a copy of the standalone tool in ../aeo-audit. Keep them in sync.

Environment variables (set by template.yaml):
    FROM_EMAIL       verified SES sender, e.g. website@dooster.io
    NOTIFY_EMAIL     Dooster inbox told about every check that runs
    ALLOWED_ORIGINS  comma-separated site origins allowed to call this
    AUDIT_PAGES      pages to sample per audit (default 10)
"""

import html
import ipaddress
import json
import os
import re
import socket
import urllib.parse
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from botocore.exceptions import ClientError

import aeo_audit

ses = boto3.client("ses")
lambda_client = boto3.client("lambda")

FROM_EMAIL = os.environ.get("FROM_EMAIL", "website@dooster.io")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "hello@dooster.io")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
_origin = ALLOWED_ORIGINS[0] if ALLOWED_ORIGINS else "*"


def _set_origin(event):
    """Answer with the caller's origin if it is allowed. API Gateway's CORS
    settings are the real guard; this keeps direct invocations consistent."""
    global _origin
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    origin = headers.get("origin", "")
    _origin = origin if origin in ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]
AUDIT_PAGES = int(os.environ.get("AUDIT_PAGES", "10"))
SITE_URL = "https://www.dooster.io"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
HOST_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": _origin,
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


# ─── Validation ───────────────────────────────────────────────────────────────

def normalise_url(raw):
    raw = (raw or "").strip()
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    host = (parts.hostname or "").lower()
    return f"{parts.scheme.lower()}://{host}{(':' + str(parts.port)) if parts.port else ''}/", host


def is_public_host(host):
    """Only audit hosts that resolve to public addresses, so the function can't
    be pointed at internal or cloud metadata addresses."""
    if not HOST_RE.match(host):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(infos)


def validate(data):
    errors, cleaned = {}, {}
    url, host = normalise_url(data.get("url"))
    if not data.get("url") or len(data["url"]) > 300:
        errors["url"] = "Please enter your website address."
    elif not is_public_host(host):
        errors["url"] = "We couldn't find that website. Please check the address."
    cleaned["url"], cleaned["host"] = url, host

    email = (data.get("email") or "").strip()
    if not EMAIL_RE.match(email) or len(email) > 200:
        errors["email"] = "Please enter a valid email address."
    cleaned["email"] = email

    for name, maxlen in (("first_name", 80), ("company", 120)):
        value = (data.get(name) or "").strip()
        if len(value) > maxlen or any(c in value for c in "\r\n"):
            errors[name] = "Please check this field."
        cleaned[name] = value
    cleaned["consent"] = bool(data.get("consent"))
    return cleaned, errors


# ─── Emails ───────────────────────────────────────────────────────────────────

def summary_html(report, name):
    e = html.escape
    rows = "".join(
        f'<tr><td style="padding:6px 12px 6px 0">{e(c["name"])}</td>'
        f'<td style="padding:6px 0;font-weight:700">{c["score"]}/100</td></tr>'
        for c in report["categories"].values())
    fixes = "".join(
        f'<li style="margin:0 0 10px"><strong>{e(c["name"])}</strong><br>'
        f'<span style="color:#585B63">{e(c["fix"])}</span></li>'
        for c in aeo_audit.top_fixes(report, 5))
    hello = f"Hi {e(name)}," if name else "Hi,"
    return f"""<html><body style="font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0D0F14;line-height:1.55;max-width:600px">
<p>{hello}</p>
<p>Here are the results of your free AI visibility check for <strong>{e(report['host'])}</strong>.</p>
<div style="background:#0D0F14;color:#fff;border-radius:16px;padding:22px 26px;margin:20px 0">
  <div style="font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:#FF5B2E;font-weight:700">AI visibility score</div>
  <div style="font-size:44px;font-weight:800;line-height:1.1">{report['score']}<span style="font-size:18px;color:#A4A7AF">/100 · grade {report['grade']}</span></div>
</div>
<table style="border-collapse:collapse;font-size:15px">{rows}</table>
<h3 style="margin:26px 0 10px">Top things to fix</h3>
<ol style="padding-left:20px;margin:0">{fixes or '<li>No major issues found.</li>'}</ol>
<p style="margin-top:24px">The full report, with every check and page-by-page findings, is attached. Open it in any browser.</p>
<p>Want help with any of this? Just reply to this email and we'll talk it through.</p>
<p>The Dooster team<br><a href="{SITE_URL}" style="color:#FF5B2E">dooster.io</a></p>
</body></html>"""


def send_report(job, report):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Your AI visibility check: {report['host']} scored {report['score']}/100"
    msg["From"] = f"Dooster <{FROM_EMAIL}>"
    msg["To"] = job["email"]
    msg["Reply-To"] = NOTIFY_EMAIL
    body = MIMEMultipart("alternative")
    top = "\n".join(f"- {c['name']}: {c['fix']}" for c in aeo_audit.top_fixes(report, 5))
    body.attach(MIMEText(
        f"Your AI visibility check for {report['host']}: {report['score']}/100 (grade {report['grade']}).\n\n"
        f"Top things to fix:\n{top}\n\nThe full report is attached.\n\nThe Dooster team\n{SITE_URL}\n", "plain", "utf-8"))
    body.attach(MIMEText(summary_html(report, job.get("first_name")), "html", "utf-8"))
    msg.attach(body)
    attachment = MIMEApplication(aeo_audit.render_html(report).encode("utf-8"), _subtype="html")
    attachment.add_header("Content-Disposition", "attachment",
                          filename=f"ai-visibility-{report['host']}.html")
    msg.attach(attachment)
    ses.send_raw_email(Source=FROM_EMAIL, Destinations=[job["email"]],
                       RawMessage={"Data": msg.as_string()})


def send_failure(job, reason):
    ses.send_email(
        Source=FROM_EMAIL,
        Destination={"ToAddresses": [job["email"]]},
        ReplyToAddresses=[NOTIFY_EMAIL],
        Message={
            "Subject": {"Data": f"We couldn't check {job['host']}", "Charset": "UTF-8"},
            "Body": {"Text": {"Charset": "UTF-8", "Data":
                f"Hi,\n\nWe tried to run your free AI visibility check on {job['url']} but couldn't load the site "
                f"({reason}).\n\nThis can happen if the address is mistyped or the site blocks automated checks. "
                f"Reply to this email and we'll run it for you by hand.\n\nThe Dooster team\n{SITE_URL}\n"}},
        })


def notify_dooster(job, report=None, error=None):
    lines = [
        f"Website:  {job['url']}",
        f"Email:    {job['email']}",
        f"Name:     {job.get('first_name') or '—'}",
        f"Company:  {job.get('company') or '—'}",
        f"Follow-up consent: {'YES' if job.get('consent') else 'no'}",
        "",
        f"Score: {report['score']}/100 (grade {report['grade']})" if report else f"Audit failed: {error}",
    ]
    if report:
        lines += [f"  {c['name']}: {c['score']}" for c in report["categories"].values()]
    ses.send_email(
        Source=FROM_EMAIL,
        Destination={"ToAddresses": [NOTIFY_EMAIL]},
        ReplyToAddresses=[job["email"]],
        Message={
            "Subject": {"Data": f"AI visibility check: {job['host']}"
                        + (f" ({report['score']}/100)" if report else " (failed)"), "Charset": "UTF-8"},
            "Body": {"Text": {"Data": "\n".join(lines), "Charset": "UTF-8"}},
        })


# ─── Entry point ──────────────────────────────────────────────────────────────

def run_job(job):
    try:
        report = aeo_audit.run_audit(job["url"], pages=AUDIT_PAGES, log=print)
    except aeo_audit.AuditError as e:
        print(f"audit failed for {job['url']}: {e}")
        send_failure(job, str(e))
        notify_dooster(job, error=str(e))
        return
    send_report(job, report)
    notify_dooster(job, report)
    print(f"audit sent: {job['host']} {report['score']}")


def lambda_handler(event, context):
    if isinstance(event, dict) and "dooster_job" in event:
        run_job(event["dooster_job"])
        return {"ok": True}

    _set_origin(event)
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod", "POST"))
    if method == "OPTIONS":
        return _response(204, {})

    try:
        data = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"ok": False, "error": "Malformed request."})
    if not isinstance(data, dict):
        return _response(400, {"ok": False, "error": "Malformed request."})

    # Honeypot: accept silently so bots don't retry
    if (data.get("website") or "").strip():
        return _response(200, {"ok": True})

    job, errors = validate(data)
    if errors:
        return _response(400, {"ok": False, "errors": errors})

    try:
        lambda_client.invoke(FunctionName=context.function_name, InvocationType="Event",
                             Payload=json.dumps({"dooster_job": job}).encode("utf-8"))
    except ClientError as e:
        print(f"could not start audit: {e}")
        return _response(502, {"ok": False, "error": "We couldn't start the check. Please email hello@dooster.io."})
    return _response(202, {"ok": True})
