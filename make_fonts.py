"""
Download the web fonts and write the @font-face rules, so the site serves them
itself instead of waiting on fonts.googleapis.com.

    python make_fonts.py

Google's own "latin" files carry about 47KB of glyphs each. The site only ever
shows English copy, so we ask Google for a subset covering the characters below
(&text=), which brings each file down to a few KB. Anything outside the set
falls back to the system font, so keep CHARS in step with the content: English
plus accented letters, currency and the punctuation and arrows the design uses.

Writes static/fonts/*.woff2 and static/css/fonts.css, both committed. The
deploy never runs this.
"""

import os
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "static", "fonts")
CSS_PATH = os.path.join(HERE, "static", "css", "fonts.css")

# Only the weights that earn their download. Text set in 500 falls back to the
# 400 face, which browsers pick automatically and looks near-identical.
FAMILIES = {"Inter": (400, 600), "Space Grotesk": (600, 700)}

CHARS = (
    "".join(chr(c) for c in range(0x20, 0x7F))          # basic latin
    + "£€$¥"                                            # currency
    + "‘’“”–—…·•"                                       # punctuation
    + "→←↑↓↗✓✕×●○"                                      # arrows and marks
    + "°™®©"
    + "áàâäãåéèêëíìîïóòôöõúùûüñçßøœæÁÀÂÄÉÈÊËÍÎÓÔÖÚÜÑÇØ"  # names and loanwords
)

# Fallback faces sized to match the real ones. With font-display: optional a
# slow first visit keeps the fallback for that whole page, so it has to take up
# the same space or the layout would differ from later visits.
FALLBACKS = """
@font-face {
  font-family: 'Inter Fallback';
  src: local('Arial'), local('Helvetica'), local('Liberation Sans');
  size-adjust: 107%;
  ascent-override: 90%;
  descent-override: 22.4%;
  line-gap-override: 0%;
}

@font-face {
  font-family: 'Grotesk Fallback';
  src: local('Arial'), local('Helvetica'), local('Liberation Sans');
  size-adjust: 103%;
  ascent-override: 96%;
  descent-override: 24%;
  line-gap-override: 0%;
}
"""

# a browser UA, or Google serves the older truetype format
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def font_url(family, weight):
    """Google's CSS for one family and weight, subset to CHARS."""
    css = fetch("https://fonts.googleapis.com/css2?" + urllib.parse.urlencode({
        "family": f"{family}:wght@{weight}",
        "text": CHARS,
        "display": "optional",
    })).decode("utf-8")
    start = css.index("src: url(") + len("src: url(")
    return css[start:css.index(")", start)]


def main():
    os.makedirs(FONT_DIR, exist_ok=True)
    rules, made = [], []

    for family, weights in FAMILIES.items():
        for weight in weights:
            name = f"{family.lower().replace(' ', '-')}-{weight}.woff2"
            with open(os.path.join(FONT_DIR, name), "wb") as f:
                f.write(fetch(font_url(family, weight)))
            made.append(name)
            rules.append(f"""@font-face {{
  font-family: '{family}';
  font-style: normal;
  font-weight: {weight};
  font-display: optional;
  src: url('/static/fonts/{name}') format('woff2');
}}""")

    with open(CSS_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("/* Self-hosted, subset web fonts — regenerate with make_fonts.py */\n\n")
        f.write("\n\n".join(rules) + "\n")
        f.write(FALLBACKS)

    total = sum(os.path.getsize(os.path.join(FONT_DIR, n)) for n in made)
    print(f"  {len(made)} fonts, {total // 1024} KB total")
    for n in sorted(made):
        print(f"    {n}  {os.path.getsize(os.path.join(FONT_DIR, n)) // 1024} KB")


if __name__ == "__main__":
    main()
