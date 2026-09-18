"""
Contact form handler — API Gateway (HTTP API) -> Lambda -> SES.

Receives a JSON enquiry from the website's contact form, validates it, and
emails it to the Dooster inbox with the enquirer's address set as Reply-To, so hitting
reply in the mail client answers the customer directly.

Environment variables (set by template.yaml):
    TO_EMAIL         where enquiries are delivered
    FROM_EMAIL       verified SES sender, e.g. website@dooster.io
    ALLOWED_ORIGIN   site origin allowed to call this, e.g. https://www.dooster.io
"""

import html
import json
import os
import re

import boto3
from botocore.exceptions import ClientError

ses = boto3.client("ses")

TO_EMAIL = os.environ.get("TO_EMAIL", "hello@dooster.io")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "website@dooster.io")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# name -> (required, max length)
FIELDS = {
    "first_name": (True, 80),
    "last_name": (False, 80),
    "email": (True, 200),
    "company": (False, 120),
    "site_url": (False, 200),
    "interest": (False, 80),
    "message": (True, 4000),
}

# Must match the interest_options values in content/pages/contact.json
INTEREST_LABELS = {
    "website": "Website design & development",
    "ai-search": "AI search & SEO",
    "social-media": "Social media management",
    "design": "UI/UX design",
    "email": "Email marketing",
    "analytics": "Analytics",
    "not-sure": "Not sure yet — needs guidance",
}


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def validate(data):
    """Return (cleaned, errors). Errors are keyed by field for inline display."""
    cleaned, errors = {}, {}

    for name, (required, maxlen) in FIELDS.items():
        value = (data.get(name) or "").strip()
        if required and not value:
            errors[name] = "This field is required."
            continue
        if len(value) > maxlen:
            errors[name] = f"Please keep this under {maxlen} characters."
            continue
        cleaned[name] = value

    email = cleaned.get("email", "")
    if email and not EMAIL_RE.match(email):
        errors["email"] = "Please enter a valid email address."

    # Newlines in the name would allow header injection into the subject line
    for name in ("first_name", "last_name"):
        if any(c in cleaned.get(name, "") for c in "\r\n"):
            errors[name] = "This field contains invalid characters."

    return cleaned, errors


def build_email(d):
    name = " ".join(filter(None, [d.get("first_name"), d.get("last_name")]))
    interest = INTEREST_LABELS.get(d.get("interest", ""), d.get("interest") or "Not specified")

    text = (
        f"New enquiry from the Dooster website\n"
        f"{'-' * 44}\n\n"
        f"Name:      {name}\n"
        f"Email:     {d.get('email')}\n"
        f"Company:   {d.get('company') or '—'}\n"
        f"Website:   {d.get('site_url') or '—'}\n"
        f"Interest:  {interest}\n\n"
        f"Message:\n{d.get('message')}\n"
    )

    body = html.escape(d.get("message", "")).replace("\n", "<br>")
    html_body = f"""<html><body style="font-family:system-ui,sans-serif;color:#0D0F14">
<h2 style="font-weight:600">New website enquiry</h2>
<table cellpadding="6" style="border-collapse:collapse;font-size:14px">
<tr><td style="color:#8C8371">Name</td><td><strong>{html.escape(name)}</strong></td></tr>
<tr><td style="color:#8C8371">Email</td>
    <td><a href="mailto:{html.escape(d.get('email',''))}">{html.escape(d.get('email',''))}</a></td></tr>
<tr><td style="color:#8C8371">Company</td><td>{html.escape(d.get('company') or '—')}</td></tr>
<tr><td style="color:#8C8371">Website</td><td>{html.escape(d.get('site_url') or '—')}</td></tr>
<tr><td style="color:#8C8371">Interest</td><td>{html.escape(interest)}</td></tr>
</table>
<p style="color:#8C8371;font-size:12px;margin-top:18px">Message</p>
<div style="border-left:2px solid #FF5B2E;padding-left:12px">{body}</div>
</body></html>"""

    return f"Website enquiry — {name or 'no name'}", text, html_body


def lambda_handler(event, context):
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

    # Honeypot: a hidden field no human fills in. Accept silently so the bot
    # believes it succeeded and doesn't retry with a different strategy.
    if (data.get("website") or "").strip():
        print("honeypot triggered, discarding submission")
        return _response(200, {"ok": True})

    cleaned, errors = validate(data)
    if errors:
        return _response(400, {"ok": False, "errors": errors})

    subject, text_body, html_body = build_email(cleaned)

    try:
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": [TO_EMAIL]},
            ReplyToAddresses=[cleaned["email"]],
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                },
            },
        )
    except ClientError as e:
        print(f"SES send failed: {e}")
        return _response(502, {
            "ok": False,
            "error": "We couldn't send your message. Please email hello@dooster.io.",
        })

    return _response(200, {"ok": True})
