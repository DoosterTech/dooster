"""
Export the Flask site to a static HTML build.

Every route is rendered through the real app, so the static build is a faithful
mirror of the dynamic site.

    python export_static.py                        # relative links — opens from the filesystem
    python export_static.py --absolute --out dist  # root-absolute links — what Amplify runs

Relative mode rewrites every internal link and asset to a path relative to the
page, so the site opens straight from the filesystem with no server. That is
the mode to use when sending the build to someone for review.
"""

import json
import os
import re
import shutil
import sys
import zipfile

import app as flaskapp

HERE = os.path.dirname(os.path.abspath(__file__))


def _out_dir():
    """Where to write the build. CI needs it inside the repo; --out overrides."""
    if "--out" in sys.argv:
        return os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
    if os.environ.get("STATIC_OUT"):
        return os.path.abspath(os.environ["STATIC_OUT"])
    return os.path.abspath(os.path.join(HERE, "..", "dooster-static"))


OUT = _out_dir()

# Old addresses live in app.REDIRECTS. On Amplify the 301s come from
# amplify-redirects.json, which the build checks against that list.
REDIRECTS = flaskapp.REDIRECTS

# Plain-text files served at the site root alongside the pages
ROOT_FILES = ("sitemap.xml", "robots.txt", "llms.txt")


def check_amplify_redirects():
    """Amplify ignores _redirects files: its rules are pasted into the console
    from amplify-redirects.json. Stop the build if a legacy URL is missing from
    that file, or if the custom 404 rule is not last — Amplify applies rules in
    order, so the catch-all has to come after everything it could swallow."""
    with open(os.path.join(HERE, "amplify-redirects.json"), encoding="utf-8") as f:
        rules = json.load(f)
    problems = []
    for old, new in REDIRECTS.items():
        if not any(r.get("source") == old and r.get("target") == new
                   and r.get("status") == "301" for r in rules):
            problems.append(f"no 301 rule for {old}")
    last = rules[-1] if rules else {}
    if not (last.get("source") == "/<*>" and last.get("status") == "404-200"):
        problems.append("the last rule must be the 404 page: source /<*>, status 404-200")
    if problems:
        print("\namplify-redirects.json is out of date:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)


def dest_for(url, flat):
    """Where a page's HTML is written.

    Zip builds use folders (/results -> results/index.html) so pages open
    straight from the filesystem.

    Hosted builds write every page twice: a flat file (results.html) and a
    folder copy (results/index.html), so /results and /results/ both resolve
    on Amplify. The canonical tag names the bare address either way.
    """
    if url == "/":
        return [os.path.join(OUT, "index.html")]
    rel = url.strip("/").replace("/", os.sep)
    folder = os.path.join(OUT, rel, "index.html")
    return [os.path.join(OUT, rel + ".html"), folder] if flat else [folder]


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


def copy_static():
    src = os.path.join(HERE, "static")
    dst = os.path.join(OUT, "static")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    total = sum(len(f) for _, _, f in os.walk(dst))
    print(f"  static assets: {total} files")


SERVE_PY = '''"""
Optional local web server for this static build.

    python serve.py            # http://localhost:4052

You do not need this to view the site — just open index.html.
"""

import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4052


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)


if __name__ == "__main__":
    print("Serving %s\\n  http://localhost:%d" % (ROOT, PORT))
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
'''

README_MD = """# Dooster — website preview

A complete copy of the Dooster website as plain HTML, CSS and JavaScript.

**Unzip the folder, then double-click `index.html`.** Every page works offline
apart from the web fonts.

The contact form doesn't send from this preview; it is connected on the hosted
site.
"""


def to_relative(html, depth):
    """Rewrite root-absolute refs to paths relative to a page at `depth`.

    depth 0 = /index.html, 1 = /results/index.html, 2 = /services/aeo/index.html
    """
    prefix = "../" * depth if depth else ""

    def rewrite(ref):
        """/results -> ../results/index.html ; /static/x.png -> ../static/x.png"""
        path, _, frag = ref.partition("#")
        if path in ("", "/"):
            out = prefix + "index.html"
        else:
            path = path.lstrip("/")
            # a real file (asset, sitemap, robots) vs a clean page URL
            if not os.path.splitext(path)[1]:
                path = path.rstrip("/") + "/index.html"
            out = prefix + path
        return out + ("#" + frag if frag else "")

    def fix_attr(m):
        attr, ref = m.group(1), m.group(2)
        if ref.startswith("//"):
            return f'{attr}="https:{ref}"'
        if not ref.startswith("/"):
            return m.group(0)
        return f'{attr}="{rewrite(ref)}"'

    return re.sub(r'(href|src)="([^"]*)"', fix_attr, html)


def main():
    relative = "--absolute" not in sys.argv

    # Content is validated first: if a content file is malformed the build
    # stops here, so the mistake never reaches the published site.
    import validate_content
    if validate_content.main() != 0:
        sys.exit(1)
    check_amplify_redirects()

    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    client = flaskapp.app.test_client()
    urls = flaskapp.page_urls()
    failures = []

    for url in urls:
        resp = client.get(url)
        if resp.status_code != 200:
            failures.append((url, resp.status_code))
            continue
        html = resp.get_data(as_text=True)
        depth = 0 if url == "/" else url.strip("/").count("/") + 1
        if relative:
            html = to_relative(html, depth)
        for dest in dest_for(url, flat=not relative):
            write(dest, html)
    mode = "relative (opens from the filesystem)" if relative else "root-absolute (needs a server)"
    print(f"  pages: {len(urls) - len(failures)}/{len(urls)}   links: {mode}   env: {flaskapp.SITE_ENV}")

    # 404 — static hosts serve /404.html by convention
    resp = client.get("/this-route-does-not-exist")
    html = resp.get_data(as_text=True)
    if relative:
        html = to_relative(html, 0)
    write(os.path.join(OUT, "404.html"), html)

    for name in ROOT_FILES:
        r = client.get("/" + name)
        if r.status_code == 200:
            write(os.path.join(OUT, name), r.get_data(as_text=True))
        else:
            failures.append(("/" + name, r.status_code))

    # support files for the shareable zip only; a hosted build must not serve them
    if relative:
        write(os.path.join(OUT, "serve.py"), SERVE_PY)
        write(os.path.join(OUT, "README.md"), README_MD)

    copy_static()

    if failures:
        print("\nFAILED:")
        for u, c in failures:
            print(f"  {c}  {u}")
        sys.exit(1)

    # Smoke-test the finished build. A broken output must never be deployed,
    # so a failure here exits non-zero and stops the Amplify deploy.
    print()
    import smoke_test
    argv = sys.argv[:]
    sys.argv = ["smoke_test", OUT]
    rc = smoke_test.main()
    sys.argv = argv
    if rc != 0:
        sys.exit(1)

    files = sum(len(f) for _, _, f in os.walk(OUT))
    print(f"\nStatic build written to {OUT}  ({files} files)")

    # Zip it for sharing — reviewers unzip and open index.html
    if relative:
        zip_path = os.path.abspath(os.path.join(OUT, "..", "dooster-website-preview.zip"))
        if os.path.exists(zip_path):
            os.remove(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, names in os.walk(OUT):
                for n in names:
                    full = os.path.join(root, n)
                    z.write(full, os.path.join("dooster-website", os.path.relpath(full, OUT)))
        print(f"Shareable zip:            {zip_path}"
              f"  ({os.path.getsize(zip_path)/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
