"""
Smoke test — checks the BUILT site before it is allowed to deploy.

    python smoke_test.py [build-dir]      (default: dist)

validate_content.py checks the source is sane. This checks the *output* is
sane: that every expected page exists, is properly formed, carries its SEO
tags and valid structured data, and that no link, image, stylesheet or script
points at something missing. Exits 1 on any failure, which stops the Amplify
deploy.

Run automatically by export_static.py; also runnable on its own.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Pages that must exist in every build, as URL paths
REQUIRED_PAGES = ["/", "/services", "/results", "/about", "/ai-visibility-check", "/contact",
                  "/privacy-policy", "/cookies-policy"]
REQUIRED_FILES = ["404.html", "sitemap.xml", "robots.txt", "llms.txt"]

# Text that must never reach production
FORBIDDEN = [
    (r"\{\{", "an unrendered template tag {{ }}"),
    (r"\{%", "an unrendered template block {% %}"),
    (r"\bLorem ipsum\b", "placeholder Lorem ipsum text"),
    (r"\bTODO\b", "a TODO note"),
    (r"\bFIXME\b", "a FIXME note"),
    (r"\bUndefined\b", "an undefined template value"),
]

MIN_PAGE_BYTES = 2000


class Failure:
    def __init__(self, where, what):
        self.where, self.what = where, what


def read(path):
    return open(path, encoding="utf-8", errors="replace").read()


def page_path(root, url):
    """A page is a flat file (hosted build) or a folder index (zip build)."""
    if url == "/":
        return os.path.join(root, "index.html")
    rel = url.strip("/").replace("/", os.sep)
    flat = os.path.join(root, rel + ".html")
    return flat if os.path.isfile(flat) else os.path.join(root, rel, "index.html")


def resolve(root, page_file, ref):
    """Resolve a link/asset ref the way a browser would, relative or absolute."""
    ref = ref.split("#")[0].split("?")[0]
    if not ref:
        return True
    if ref.startswith("/"):
        target = os.path.join(root, ref.lstrip("/").replace("/", os.sep))
    else:
        target = os.path.normpath(os.path.join(os.path.dirname(page_file),
                                               ref.replace("/", os.sep)))
    return (os.path.isfile(target) or os.path.isfile(os.path.join(target, "index.html"))
            or os.path.isfile(target.rstrip(os.sep) + ".html"))


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dist"))
    if not os.path.isdir(root):
        print(f"No build found at {root}. Run export_static.py first.")
        return 1

    failures = []
    services = json.load(open(os.path.join(HERE, "content", "services.json"),
                              encoding="utf-8"))["services"]
    required = REQUIRED_PAGES + [s["url"] for s in services]

    # 1. every required page exists and is big enough
    for url in required:
        p = page_path(root, url)
        if not os.path.isfile(p):
            failures.append(Failure(url, "the page is missing from the build"))
            continue
        size = os.path.getsize(p)
        if size < MIN_PAGE_BYTES:
            failures.append(Failure(url, f"the page is only {size} bytes — it looks empty or broken"))

    for f in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(root, f)):
            failures.append(Failure(f, "the file is missing from the build"))

    # 2. structural + SEO checks on every built page
    html_files = []
    for d, _, files in os.walk(root):
        html_files += [os.path.join(d, f) for f in files if f.endswith(".html")]

    for f in html_files:
        rel = os.path.relpath(f, root).replace(os.sep, "/")
        html = read(f)
        is404 = rel == "404.html"

        if "<!doctype" not in html.lower():
            failures.append(Failure(rel, "missing a doctype — the file may be truncated"))
        if "</html>" not in html.lower():
            failures.append(Failure(rel, "the closing </html> tag is missing — output was cut short"))
        if not re.search(r"<title>.{5,}?</title>", html, re.S):
            failures.append(Failure(rel, "the page has no usable <title>"))
        if "<footer" not in html:
            failures.append(Failure(rel, "the footer is missing"))
        if "<header" not in html:
            failures.append(Failure(rel, "the header is missing"))
        if len(re.findall(r"<h1[\s>]", html)) != 1:
            failures.append(Failure(rel, "the page should have exactly one <h1>"))
        if not is404:
            if 'name="description"' not in html:
                failures.append(Failure(rel, "the meta description is missing"))
            if 'rel="canonical"' not in html:
                failures.append(Failure(rel, "the canonical link is missing"))

        for pattern, label in FORBIDDEN:
            if re.search(pattern, html):
                failures.append(Failure(rel, f"the page contains {label}"))

        # 3. structured data must parse — broken JSON-LD is ignored by search and AI engines
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            try:
                json.loads(block)
            except json.JSONDecodeError as e:
                failures.append(Failure(rel, f"a JSON-LD block is invalid: {e.msg}"))

        # 4. every internal link and asset resolves
        for ref in set(re.findall(r'(?:href|src)="([^"]+)"', html)):
            if ref.startswith(("http://", "https://", "mailto:", "tel:", "#", "data:", "//")):
                continue
            if not resolve(root, f, ref):
                failures.append(Failure(rel, f"the link or asset '{ref}' points to something missing"))

    # 5. the stylesheet actually made it and isn't a stub
    css = os.path.join(root, "static", "css", "base.css")
    if not os.path.isfile(css):
        failures.append(Failure("static/css/base.css", "the stylesheet is missing"))
    elif os.path.getsize(css) < 10000:
        failures.append(Failure("static/css/base.css",
                                f"the stylesheet is only {os.path.getsize(css)} bytes — it looks incomplete"))

    # 6. sitemap lists the main pages
    sm = os.path.join(root, "sitemap.xml")
    if os.path.isfile(sm):
        sitemap = read(sm)
        for url in required:
            if url != "/" and url not in sitemap:
                failures.append(Failure("sitemap.xml", f"{url} is missing from the sitemap"))

    # 7. environment. The live site must be indexable; a dev build is a public
    #    URL too, so it must keep itself out of search.
    from site_env import IS_PRODUCTION, SITE_ENV
    robots_file = os.path.join(root, "robots.txt")
    robots = read(robots_file) if os.path.isfile(robots_file) else ""
    blocks_all = bool(re.search(r"(?m)^Disallow:\s*/\s*$", robots))

    for f in html_files:
        rel = os.path.relpath(f, root).replace(os.sep, "/")
        if rel == "404.html":
            continue      # noindex in every environment, deliberately
        noindex = bool(re.search(r'name="robots"[^>]*noindex', read(f)))
        if IS_PRODUCTION and noindex:
            failures.append(Failure(rel, "a production page tells search engines not to index it"))
        if not IS_PRODUCTION and not noindex:
            failures.append(Failure(rel, "a dev page is missing noindex, so it could appear in search"))

    if IS_PRODUCTION:
        if blocks_all:
            failures.append(Failure("robots.txt", "a production build is blocking every crawler"))
        for name in ("README.md", "serve.py"):
            if os.path.exists(os.path.join(root, name)):
                failures.append(Failure(name, "an internal review file is in the production build"))
    elif not blocks_all:
        failures.append(Failure("robots.txt", "a dev build must block crawlers with Disallow: /"))

    total = len(html_files)
    if not failures:
        print(f"Smoke test passed — {total} pages checked in {os.path.basename(root)}/  ({SITE_ENV})")
        print(f"  {len(required)} core pages, structured data valid, links and assets all resolve")
        return 0

    by_page = {}
    for f in failures:
        by_page.setdefault(f.where, []).append(f.what)

    print(f"SMOKE TEST FAILED — {len(failures)} problem(s) across {len(by_page)} page(s):")
    for where, whats in list(by_page.items())[:25]:
        print(f"\n  {where}")
        for w in whats[:6]:
            print(f"     - {w}")
        if len(whats) > 6:
            print(f"     ... and {len(whats) - 6} more")
    if len(by_page) > 25:
        print(f"\n  ... and {len(by_page) - 25} more pages with problems")
    print("\nThe build is broken and must not be deployed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
