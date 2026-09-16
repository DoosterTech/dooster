"""
Check every content file before the site is built.

    python validate_content.py

Exits 0 if the content is sound, 1 if anything is wrong — printing the file,
what's wrong and how to fix it. export_static.py runs this first, so a mistake
in a content file can never reach the published site: the build stops and
whatever is already live stays live.

Safe for non-technical editors to run; the output is written for them.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.join(HERE, "content")
PAGES = os.path.join(CONTENT, "pages")
LEGAL = os.path.join(CONTENT, "legal")
IMAGES = os.path.join(HERE, "static", "images")

# Keys that collide with Python dict methods and silently break Jinja lookups
RESERVED_KEYS = {"items", "keys", "values", "get", "pop", "update", "copy"}

SERVICE_REQUIRED = ["slug", "url", "icon", "short_title", "title", "card_title", "summary",
                    "description", "hero", "definition", "problems", "approach", "process",
                    "deliverables", "ideal_for", "faqs", "cta"]
KNOWN_ICONS = {"aeo", "web", "social", "design", "email", "analytics"}


class Problem:
    def __init__(self, path, line, what, fix):
        self.path, self.line, self.what, self.fix = path, line, what, fix

    def show(self):
        loc = self.path + (f"  (line {self.line})" if self.line else "")
        print(f"\n  {loc}")
        print(f"     problem: {self.what}")
        print(f"     fix    : {self.fix}")


def image_exists(name):
    return os.path.isfile(os.path.join(IMAGES, name.replace("/", os.sep)))


def known_routes():
    routes = {"/", "/services", "/results", "/contact"}
    services = json.load(open(os.path.join(CONTENT, "services.json"), encoding="utf-8"))
    routes |= {s["url"] for s in services["services"] if s.get("url")}
    if os.path.isdir(LEGAL):
        routes |= {"/" + f[:-3] for f in os.listdir(LEGAL) if f.endswith(".md")}
    return routes


def check_link(href, rel, routes, problems):
    """An internal link must point at a real page (an #anchor on it is fine)."""
    if not isinstance(href, str) or not href.startswith("/"):
        return
    path = href.split("#")[0].rstrip("/") or "/"
    if path not in routes:
        problems.append(Problem(rel, None,
            f"the link {href} isn't a page on the site",
            "check the address, or use the full https://... URL if it's external"))


def walk_json(data, rel, problems, routes, path=""):
    """Recursively check a content JSON file for reserved keys, bad images and links."""
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else key
            if key in RESERVED_KEYS:
                problems.append(Problem(rel, None,
                    f"'{here}' uses the reserved name '{key}'",
                    f"rename it — '{key}' clashes with a built-in and breaks the page"))
            if key == "image" and isinstance(value, str) and value and not image_exists(value):
                problems.append(Problem(rel, None, f"the image '{value}' doesn't exist",
                                        "check the file is in static/images/"))
            if key == "href":
                check_link(value, rel, routes, problems)
            walk_json(value, rel, problems, routes, here)
    elif isinstance(data, list):
        for i, value in enumerate(data):
            walk_json(value, rel, problems, routes, f"{path}[{i}]")


def load_json(path, problems):
    rel = os.path.relpath(path, HERE)
    try:
        return json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        problems.append(Problem(rel, e.lineno, f"the file isn't valid JSON — {e.msg}",
                                "check for a missing comma, quote or bracket near that line"))
        return None


def check_json_file(path, routes, required_top=()):
    problems = []
    data = load_json(path, problems)
    if data is None:
        return problems
    rel = os.path.relpath(path, HERE)
    for key in required_top:
        if key not in data:
            problems.append(Problem(rel, None, f"the '{key}' section is missing",
                                    f"add a top-level \"{key}\" block"))
    walk_json(data, rel, problems, routes)
    return problems


def check_services(routes):
    rel = "content/services.json"
    problems = []
    data = load_json(os.path.join(CONTENT, "services.json"), problems) or {"services": []}
    seen = set()
    for s in data.get("services", []):
        name = s.get("slug", "?")
        for key in SERVICE_REQUIRED:
            if not s.get(key):
                problems.append(Problem(rel, None, f"the service '{name}' is missing '{key}'",
                                        f'add  "{key}": ...  to that service'))
        if not str(s.get("url", "")).startswith("/services/"):
            problems.append(Problem(rel, None, f"the service '{name}' has an unexpected url",
                                    'service urls look like  "/services/<slug>"'))
        if s.get("icon") and s["icon"] not in KNOWN_ICONS:
            problems.append(Problem(rel, None, f"the service '{name}' uses an unknown icon '{s['icon']}'",
                                    f"use one of: {', '.join(sorted(KNOWN_ICONS))}"))
        if name in seen:
            problems.append(Problem(rel, None, f"two services share the slug '{name}'",
                                    "every service needs its own slug"))
        seen.add(name)
        if len(s.get("description", "")) > 300:
            problems.append(Problem(rel, None,
                f"the description for '{name}' is {len(s['description'])} characters",
                "keep meta descriptions under about 300 characters"))
    return problems


def check_legal(path):
    """A legal page needs a title and a description; the body is the policy text."""
    rel = os.path.relpath(path, HERE)
    raw = open(path, encoding="utf-8").read()
    parts = raw.split("---", 2)
    if not raw.startswith("---") or len(parts) < 3:
        return [Problem(rel, 1, "the settings block at the top is missing or not closed",
                        "start and end it with a line of three dashes: ---")]
    meta = {}
    for line in parts[1].strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    problems = [Problem(rel, None, f"the '{key}' setting is missing or empty",
                        f"add a line near the top:  {key}: ...")
                for key in ("title", "description") if not meta.get(key)]
    if len(parts[2].strip()) < 200:
        problems.append(Problem(rel, None, "the page text is nearly empty",
                                "add the policy text below the second --- line"))
    return problems


def main():
    problems = []
    routes = known_routes()

    legal = sorted(f for f in os.listdir(LEGAL) if f.endswith(".md")) if os.path.isdir(LEGAL) else []
    for f in legal:
        problems += check_legal(os.path.join(LEGAL, f))
        body = open(os.path.join(LEGAL, f), encoding="utf-8").read()
        for href in re.findall(r"\]\((/[^)]*)\)", body):
            check_link(href, f"content/legal/{f}", routes, problems)

    problems += check_json_file(os.path.join(CONTENT, "site.json"), routes,
                                ("brand", "contact", "nav", "footer", "site_url", "seo"))
    problems += check_json_file(os.path.join(CONTENT, "services.json"), routes, ("services",))
    problems += check_services(routes)
    problems += check_json_file(os.path.join(CONTENT, "case-studies.json"), routes, ("case_studies",))

    page_files = sorted(f for f in os.listdir(PAGES) if f.endswith(".json"))
    for f in page_files:
        problems += check_json_file(os.path.join(PAGES, f), routes)

    checked = f"{len(page_files)} page files, {len(legal)} legal pages, site/services/case studies"
    if not problems:
        print(f"All content is valid  ({checked})")
        return 0

    print(f"Found {len(problems)} problem(s) in {checked}:")
    for p in problems:
        p.show()
    print("\nThe site was NOT rebuilt. Fix the above and run this again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
