"""
Download the web fonts and write the @font-face rules, so the site serves them
itself instead of waiting on fonts.googleapis.com.

    python make_fonts.py

Writes static/fonts/*.woff2 and static/css/fonts.css. Both are committed; the
deploy does not run this. Only the latin subsets are kept, which is what the
site's copy needs.
"""

import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "static", "fonts")
CSS_PATH = os.path.join(HERE, "static", "css", "fonts.css")

GOOGLE_CSS = ("https://fonts.googleapis.com/css2"
              "?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap")
# a browser UA, or Google serves the older truetype format
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
# the block that covers English text; the others (Cyrillic, Greek, Vietnamese)
# would only add weight
LATIN = "U+0000-00FF"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main():
    os.makedirs(FONT_DIR, exist_ok=True)
    css = fetch(GOOGLE_CSS).decode("utf-8")

    rules, kept = [], []
    for block in re.findall(r"@font-face\s*\{[^}]*\}", css):
        if LATIN not in block:
            continue
        family = re.search(r"font-family: '([^']+)'", block).group(1)
        weight = re.search(r"font-weight: (\d+)", block).group(1)
        url = re.search(r"src: url\((https://[^)]+)\)", block).group(1)

        name = f"{family.lower().replace(' ', '-')}-{weight}.woff2"
        with open(os.path.join(FONT_DIR, name), "wb") as f:
            f.write(fetch(url))
        kept.append(name)
        rules.append(f"""@font-face {{
  font-family: '{family}';
  font-style: normal;
  font-weight: {weight};
  font-display: swap;
  src: url('/static/fonts/{name}') format('woff2');
  unicode-range: {LATIN}, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC,
    U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}}""")

    with open(CSS_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("/* Self-hosted web fonts — regenerate with make_fonts.py */\n\n")
        f.write("\n\n".join(rules) + "\n")

    total = sum(os.path.getsize(os.path.join(FONT_DIR, n)) for n in kept)
    print(f"  {len(kept)} fonts, {total // 1024} KB total")
    for n in sorted(kept):
        print(f"    {n}  {os.path.getsize(os.path.join(FONT_DIR, n)) // 1024} KB")


if __name__ == "__main__":
    main()
