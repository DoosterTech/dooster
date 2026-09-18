"""
Dooster website.

All site text lives in content/ as JSON and markdown — nothing editorial is
hard-coded in templates. Run `python export_static.py` to produce the static
build, or run this file directly for local development.
"""

from flask import Flask, render_template, abort, Response, redirect
import copy
import datetime
import json
import os
import re

import markdown as md

from site_env import IS_PRODUCTION, SITE_ENV

app = Flask(__name__)

CONTENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "content")
LEGAL_DIR = os.path.join(CONTENT, "legal")

# Endpoint the contact form posts to. Set in Amplify; empty locally, which
# makes the form show a "not connected" notice instead of failing silently.
FORM_ENDPOINT = os.environ.get("FORM_ENDPOINT", "")
# Endpoint for the free AI visibility check (same pattern as the contact form).
AUDIT_ENDPOINT = os.environ.get("AUDIT_ENDPOINT", "")

_cache = {}


def _load_json(*parts):
    """Load a JSON file from content/, cached outside debug mode."""
    key = "/".join(parts)
    if key in _cache and not app.debug:
        return _cache[key]
    with open(os.path.join(CONTENT, *parts), encoding="utf-8") as f:
        data = json.load(f)
    _cache[key] = data
    return data


def load_site():
    return _load_json("site.json")


def load_page(name):
    return _load_json("pages", f"{name}.json")


def load_services():
    return _load_json("services.json")["services"]


def load_case_studies():
    return _load_json("case-studies.json")["case_studies"]


def _frontmatter(raw):
    _, frontmatter, body = raw.split("---", 2)
    meta = {}
    for line in frontmatter.strip().splitlines():
        key, _sep, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body.strip()


def load_legal():
    """Legal pages from content/legal/*.md, each served at /<filename>."""
    if "legal" in _cache and not app.debug:
        return _cache["legal"]
    pages = []
    if os.path.isdir(LEGAL_DIR):
        for fname in sorted(os.listdir(LEGAL_DIR)):
            if not fname.endswith(".md"):
                continue
            with open(os.path.join(LEGAL_DIR, fname), encoding="utf-8") as f:
                meta, body = _frontmatter(f.read())
            slug = fname[:-3]
            pages.append({
                "slug": slug,
                "url": f"/{slug}",
                "title": meta["title"],
                "description": meta.get("description", ""),
                "html": md.markdown(body, extensions=["tables"]),
            })
    _cache["legal"] = pages
    return pages


def build_meta(page_meta, site):
    """Expand a page's meta block into absolute URLs for canonical/OG tags."""
    base = site["site_url"]
    path = page_meta.get("path", "")
    return {
        "title": page_meta["title"],
        "description": page_meta.get("description", ""),
        "url": f"{base}/{path}".rstrip("/"),
        "image": f"{base}/static/images/{page_meta.get('image', 'og-default.png')}",
    }


# ─── Addresses ────────────────────────────────────────────────────────────────

# Old addresses that no longer have a page of their own, and where each goes.
# Amplify serves these as 301s from amplify-redirects.json; export_static.py
# fails the build if that file and this list ever disagree.
REDIRECTS = {}


def page_urls():
    """Every page address the site serves — the static build and the sitemap
    both come from this, so they cannot list different pages."""
    urls = ["/", "/services", "/results", "/about", "/ai-visibility-check", "/contact"]
    urls += [s["url"] for s in load_services()]
    urls += [l["url"] for l in load_legal()]
    return urls


def _endpoint(prefix, path):
    return prefix + re.sub(r"\W", "_", path)


def nav_for(site):
    """Expand any nav item marked children_from:services using services.json,
    so the menu updates itself when a service is added or renamed."""
    nav = copy.deepcopy(site["nav"])
    for item in nav["primary"]:
        if item.pop("children_from", None) != "services":
            continue
        item["children"] = [{"label": "All services", "href": "/services"}] + [
            {"label": s["short_title"], "href": s["url"]} for s in load_services()
        ]
    return nav


@app.context_processor
def inject_globals():
    """Make site-wide content available to every template."""
    site = load_site()
    return {"site": site, "nav": nav_for(site), "all_services": load_services(),
            "form_endpoint": FORM_ENDPOINT, "audit_endpoint": AUDIT_ENDPOINT, "site_env": SITE_ENV,
            "is_production": IS_PRODUCTION}


@app.template_filter("nice_date")
def nice_date(value):
    """2026-09-17 -> 17 September 2026"""
    d = datetime.date.fromisoformat(value)
    return f"{d.day} {d.strftime('%B %Y')}"


def render_page(template, name, **extra):
    site = load_site()
    page = load_page(name)
    return render_template(template, page=page,
                           meta=build_meta(page["meta"], site), **extra)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_page("pages/home.html", "home",
                       services=load_services(), case_studies=load_case_studies())


@app.route("/services")
def services():
    return render_page("pages/services.html", "services", services=load_services())


def _service_page(slug):
    service = next((s for s in load_services() if s["slug"] == slug), None)
    if not service:
        abort(404)
    site = load_site()
    related = [s for s in load_services() if s["slug"] != slug][:3]
    meta = build_meta({
        "title": f'{service["title"]} | Dooster',
        "description": service["description"],
        "path": service["url"].lstrip("/"),
        "image": "og-default.png",
    }, site)
    return render_template("pages/service-detail.html", service=service,
                           related=related, labels=load_page("service-detail")["labels"],
                           meta=meta)


for _s in load_services():
    app.add_url_rule(_s["url"], endpoint=_endpoint("service", _s["slug"]),
                     view_func=(lambda slug=_s["slug"]: _service_page(slug)))


@app.route("/results")
def results():
    return render_page("pages/results.html", "results", case_studies=load_case_studies())


@app.route("/about")
def about():
    return render_page("pages/about.html", "about", case_studies=load_case_studies())


@app.route("/ai-visibility-check")
def ai_visibility_check():
    return render_page("pages/ai-visibility-check.html", "ai-visibility-check")


@app.route("/contact")
def contact():
    return render_page("pages/contact.html", "contact")


def _legal_page(slug):
    page = next((p for p in load_legal() if p["slug"] == slug), None)
    if not page:
        abort(404)
    meta = build_meta({
        "title": f'{page["title"]} | Dooster',
        "description": page["description"],
        "path": slug,
        "image": "og-default.png",
    }, load_site())
    return render_template("pages/legal.html", legal=page, meta=meta)


for _l in load_legal():
    app.add_url_rule(_l["url"], endpoint=_endpoint("legal", _l["slug"]),
                     view_func=(lambda slug=_l["slug"]: _legal_page(slug)))


for _old, _new in REDIRECTS.items():
    app.add_url_rule(_old, endpoint=_endpoint("redirect", _old),
                     view_func=(lambda new=_new: redirect(new, 301)))


@app.errorhandler(404)
def not_found(e):
    site = load_site()
    page = load_page("404")
    return render_template("pages/404.html", page=page,
                           meta=build_meta(page["meta"], site)), 404


# sitemap weighting by page; services and legal pages fall back to defaults below
SITEMAP_WEIGHTS = {
    "/": ("1.0", "weekly"), "/services": ("0.9", "monthly"),
    "/results": ("0.8", "monthly"), "/about": ("0.7", "monthly"),
    "/ai-visibility-check": ("0.8", "monthly"), "/contact": ("0.7", "yearly"),
}


@app.route("/sitemap.xml")
def sitemap():
    base = load_site()["site_url"]
    service_urls = {s["url"] for s in load_services()}
    pages = []
    for url in page_urls():
        if url in SITEMAP_WEIGHTS:
            priority, freq = SITEMAP_WEIGHTS[url]
        elif url in service_urls:
            priority, freq = "0.8", "monthly"
        else:
            priority, freq = "0.3", "yearly"
        pages.append({"url": base if url == "/" else base + url,
                      "priority": priority, "changefreq": freq})
    return Response(render_template("sitemap.xml", pages=pages),
                    mimetype="application/xml")


# AI crawlers named explicitly: an AEO agency's own site should welcome them.
AI_CRAWLERS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot",
               "ClaudeBot", "Claude-SearchBot", "Google-Extended", "Applebot-Extended",
               "Bingbot"]


@app.route("/robots.txt")
def robots():
    if not IS_PRODUCTION:
        # a dev deploy is a public URL, so keep crawlers out of it entirely
        return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")
    base = load_site()["site_url"]
    lines = [f"User-agent: {bot}\nAllow: /\n" for bot in AI_CRAWLERS]
    lines.append(f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/llms.txt")
def llms_txt():
    """A plain-text summary of the site for LLMs (llmstxt.org), built from the
    same content as the pages so it never drifts."""
    return Response(render_template("llms.txt", services=load_services(),
                                    case_studies=load_case_studies(),
                                    about=load_page("about")),
                    mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4051, debug=True)
