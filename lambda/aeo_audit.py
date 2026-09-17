"""
AEO audit — checks how ready a website is to be found, understood and cited by
AI answer engines (ChatGPT, Perplexity, Gemini, Copilot, Google AI Overviews),
and writes an HTML report with a score and prioritised fixes.

    python aeo_audit.py https://www.example.com
    python aeo_audit.py https://www.example.com --pages 30 --brand "Example Ltd"
    python aeo_audit.py https://www.example.com --questions questions.txt

Standard library only — nothing to install.

What it checks
  1. AI crawler access   robots.txt rules per AI bot, firewall/CDN blocking,
                         HTTPS, sitemap, content present without JavaScript
  2. Content structure   headings, question-led sections, answer-first copy,
                         lists/tables, depth, titles and descriptions
  3. Structured data     JSON-LD validity, Organization + sameAs, FAQPage,
                         breadcrumbs, canonical and Open Graph tags
  4. Freshness & trust   dates, authors, about/contact pages, language

AI visibility (are you actually named in AI answers?) is reported separately:
  - with PERPLEXITY_API_KEY set, each question is asked to Perplexity's API and
    the answer is checked for your brand and domain
  - otherwise the report lists the questions with one-click links to run them
    in ChatGPT, Perplexity, Google and Bing by hand
"""

import argparse
import concurrent.futures
import datetime
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import webbrowser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

VERSION = "1.0"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 aeo-audit/" + VERSION)

# Bots that fetch pages to answer a user's question right now: blocking these
# means the site cannot be cited.
SEARCH_BOTS = ["OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Claude-SearchBot",
               "Bingbot", "Googlebot"]
# Bots that collect training data: blocking them is a business choice, but it
# reduces what models "know" about the brand.
TRAINING_BOTS = ["GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "CCBot"]

# User agents used to spot firewall/CDN rules that reject AI crawlers.
BOT_UAS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
}
CHALLENGE_MARKERS = ["cf-chl", "challenge-platform", "just a moment...", "attention required",
                     "captcha", "access denied", "bot protection"]

# "What is…", "How much…", "How to…" read as questions; "How we work" / "What you get" do not.
QUESTION_START = re.compile(
    r"^(what|how|why|when|where|who|which)\s+(is|are|was|were|do|does|did|can|could|should|will|would|"
    r"much|many|long|often|to|makes?|happens|type|kind|size|cost)\b", re.I)
WORD = re.compile(r"[A-Za-z0-9À-ɏ][A-Za-z0-9À-ɏ'’-]*")
# Account, cart and search pages are never citation targets, so they aren't sampled.
UTILITY_PATH = re.compile(r"/(cart|basket|checkout|login|log-in|signin|sign-in|signup|sign-up|register|account|"
                          r"my-account|password|logout|wp-admin|wp-login|search)(/|$|\?)", re.I)
NON_HTML_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|pptx?|mp4|mp3|xml|txt|json|css|js)$", re.I)

ORG_TYPES = {"Organization", "Corporation", "LocalBusiness", "ProfessionalService", "OnlineBusiness",
             "NGO", "EducationalOrganization", "FinancialService", "LegalService", "MedicalOrganization"}
ARTICLE_TYPES = {"Article", "BlogPosting", "NewsArticle", "TechArticle", "Report"}

CATEGORIES = [
    ("access", "AI crawler access", 30),
    ("content", "Content structure", 30),
    ("schema", "Structured data & entity", 25),
    ("trust", "Freshness & trust", 15),
]
FACTOR = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
IMPACT_ORDER = {"High": 0, "Medium": 1, "Low": 2}

SSL_CTX = ssl.create_default_context()


# ─── Fetching ─────────────────────────────────────────────────────────────────

class Response:
    def __init__(self, url, status=0, final_url="", headers=None, body=b"", elapsed=0.0, error=""):
        self.url, self.status, self.final_url = url, status, final_url or url
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body, self.elapsed, self.error = body, elapsed, error

    @property
    def text(self):
        m = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""), re.I)
        try:
            return self.body.decode(m.group(1) if m else "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    @property
    def is_html(self):
        ctype = self.headers.get("content-type", "")
        return "html" in ctype or (not ctype and b"<html" in self.body[:2000].lower())


def fetch(url, ua=BROWSER_UA, timeout=20, max_bytes=6_000_000):
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            body = r.read(max_bytes)
            return Response(url, r.status, r.geturl(), dict(r.headers), body, time.time() - start)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(max_bytes)
        except Exception:
            body = b""
        return Response(url, e.code, url, dict(e.headers or {}), body, time.time() - start)
    except Exception as e:  # DNS, TLS, timeout, connection refused
        return Response(url, 0, url, {}, b"", time.time() - start, error=str(e))


def host_key(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def normalise(url):
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


# ─── HTML parsing ─────────────────────────────────────────────────────────────

class PageParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "iframe"}
    BREAK = {"p", "div", "li", "br", "tr", "td", "th", "section", "article", "ul", "ol", "table",
             "header", "footer", "nav", "main", "aside", "dd", "dt", "blockquote", "figcaption",
             "summary", "details", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.lang, self.canonical = "", "", ""
        self.meta, self.headings, self.sections = {}, [], []
        self.jsonld_raw, self.links, self.text_parts = [], [], []
        self.lists = self.tables = self.images = self.images_alt = self.time_tags = 0
        self._in_title, self._skip, self._heading, self._jsonld = False, 0, None, None
        self._in_link = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = a.get("href", "")
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self._jsonld = []
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            # a heading inside a link is a card title pointing elsewhere, not a section
            self._heading = [int(tag[1]), [], self._in_link > 0]
        elif tag in ("ul", "ol"):
            self.lists += 1
        elif tag == "table":
            self.tables += 1
        elif tag == "img":
            self.images += 1
            if "alt" in a:
                self.images_alt += 1
        elif tag == "a":
            self._in_link += 1
            if a.get("href"):
                self.links.append(a["href"])
        elif tag == "time":
            self.time_tags += 1

        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BREAK:
            self._newline()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._jsonld is not None:
            self.jsonld_raw.append("".join(self._jsonld))
            self._jsonld = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading:
            text = clean(" ".join(self._heading[1]))
            if text:
                self.headings.append((self._heading[0], text))
                self.sections.append({"level": self._heading[0], "heading": text, "text": [],
                                      "linked": self._heading[2]})
            self._heading = None
        elif tag == "a":
            self._in_link = max(0, self._in_link - 1)
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        if tag in self.BREAK:
            self._newline()

    def handle_data(self, data):
        if self._jsonld is not None:
            self._jsonld.append(data)
        elif self._in_title:
            self.title += data
        elif self._skip:
            return
        elif self._heading:
            self._heading[1].append(data)
        else:
            self.text_parts.append(data)
            if self.sections:
                self.sections[-1]["text"].append(data)

    def _newline(self):
        self.text_parts.append("\n")
        if self.sections:
            self.sections[-1]["text"].append("\n")


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def words(s):
    return len(WORD.findall(s or ""))


def is_question(heading):
    h = heading.strip()
    return h.endswith("?") or bool(QUESTION_START.match(h))


def first_block(text):
    """The first real line of prose under a heading (skips dates, labels, buttons)."""
    for line in text.split("\n"):
        line = clean(line)
        if words(line) >= 6:
            return line
    return ""


def parse_jsonld(raws):
    items, invalid = [], 0
    for raw in raws:
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            invalid += 1
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                if "@type" in x:
                    items.append(x)
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
    return items, invalid


def types_of(item):
    t = item.get("@type", [])
    return {t} if isinstance(t, str) else {str(x) for x in t}


def is_org(item):
    return any(t in ORG_TYPES or t.endswith("Organization") or t.endswith("Business")
               for t in types_of(item))


# ─── Page analysis ────────────────────────────────────────────────────────────

def analyse_page(resp, site_host):
    p = PageParser()
    try:
        p.feed(resp.text)
    except Exception:
        pass
    text = "".join(p.text_parts)
    items, invalid = parse_jsonld(p.jsonld_raw)
    types = sorted({t for i in items for t in types_of(i)})

    # A heading followed straight away by deeper sub-headings is a section title
    # ("Why growth stalls" → cards with H3s), not a question awaiting an answer.
    q_sections = []
    for i, s in enumerate(p.sections):
        if s["linked"] or not is_question(s["heading"]):
            continue
        nxt = p.sections[i + 1] if i + 1 < len(p.sections) else None
        if not first_block("".join(s["text"])) and nxt and nxt["level"] > s["level"]:
            continue
        q_sections.append(s)
    answer_ok = 0
    for s in q_sections:
        block = first_block("".join(s["text"]))
        first_sentence = re.split(r"(?<=[.!?])\s+", block)[0] if block else ""
        if words(block) >= 8 and words(first_sentence) <= 45:
            answer_ok += 1

    robots_meta = (p.meta.get("robots", "") + " " + resp.headers.get("x-robots-tag", "")).lower()
    raw = resp.text
    has_date = bool(
        p.time_tags
        or re.search(r'"date(Modified|Published)"', raw)
        or "article:modified_time" in p.meta or "article:published_time" in p.meta
        or re.search(r"(last\s+)?updated[:\s]+\w*\s*\d{1,2}[\s/.-]\w+[\s/.-]\d{2,4}", text, re.I))

    internal = set()
    for href in p.links:
        absu = urllib.parse.urljoin(resp.final_url, href)
        if absu.startswith("http") and host_key(absu) == site_host:
            internal.add(normalise(absu.split("#")[0]))

    articles = [i for i in items if types_of(i) & ARTICLE_TYPES]
    return {
        "url": resp.final_url,
        "status": resp.status,
        "elapsed": round(resp.elapsed, 2),
        "error": resp.error,
        "title": clean(p.title),
        "description": clean(p.meta.get("description", "")),
        "canonical": p.canonical,
        "lang": p.lang,
        "noindex": "noindex" in robots_meta,
        "og": bool(p.meta.get("og:title") and p.meta.get("og:description")),
        "words": words(text),
        "h1": sum(1 for lvl, _ in p.headings if lvl == 1),
        "headings": len(p.headings),
        "question_headings": [s["heading"] for s in q_sections],
        "answer_first_ok": answer_ok,
        "lists": p.lists,
        "tables": p.tables,
        "images": p.images,
        "images_alt": p.images_alt,
        "jsonld_types": types,
        "jsonld_invalid": invalid,
        "jsonld_items": items,
        "has_date": has_date,
        "is_article": bool(articles),
        "article_has_author": all("author" in a for a in articles) if articles else None,
        "meta_author": bool(p.meta.get("author")),
        "internal_links": sorted(internal),
    }


# ─── Discovery ────────────────────────────────────────────────────────────────

def read_robots(base):
    resp = fetch(urllib.parse.urljoin(base, "/robots.txt"))
    rp = urllib.robotparser.RobotFileParser()
    if resp.status == 200:
        body = resp.text
        rp.parse(body.splitlines())
        sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", body)
        return rp, resp.status, body, sitemaps
    if resp.status in (401, 403):
        rp.disallow_all = True
    else:
        rp.allow_all = True
    return rp, resp.status, "", []


def sitemap_urls(base, declared, limit=2000):
    candidates = declared + [urllib.parse.urljoin(base, "/sitemap.xml"),
                             urllib.parse.urljoin(base, "/sitemap_index.xml")]
    seen, urls, found_at = set(), [], ""
    queue = list(dict.fromkeys(candidates))
    while queue and len(urls) < limit and len(seen) < 12:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        resp = fetch(sm)
        if resp.status != 200 or b"<" not in resp.body[:500]:
            continue
        try:
            root = ET.fromstring(resp.body.lstrip(b"\xef\xbb\xbf").strip())
        except ET.ParseError:
            continue
        found_at = found_at or sm
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
        if root.tag.endswith("sitemapindex"):
            queue.extend(locs[:10])
        else:
            urls.extend(locs)
    return found_at, list(dict.fromkeys(urls))[:limit]


def choose_pages(home, candidates, n, site_host):
    """Homepage first, then a spread of shallow pages from different sections."""
    pool = []
    for u in candidates:
        path = urllib.parse.urlsplit(u).path
        if host_key(u) != site_host or NON_HTML_EXT.search(path) or UTILITY_PATH.search(path):
            continue
        pool.append(normalise(u))
    pool = [u for u in dict.fromkeys(pool) if u != normalise(home)]
    pool.sort(key=lambda u: (urllib.parse.urlsplit(u).path.strip("/").count("/"), len(u)))
    chosen, sections = [normalise(home)], {}
    for u in pool:  # first pass: one page per top-level section
        top = urllib.parse.urlsplit(u).path.strip("/").split("/")[0]
        if top not in sections:
            sections[top] = u
            chosen.append(u)
        if len(chosen) >= n:
            return chosen
    for u in pool:
        if u not in chosen:
            chosen.append(u)
        if len(chosen) >= n:
            break
    return chosen


# ─── Checks ───────────────────────────────────────────────────────────────────

def check(cid, category, name, status, finding, fix, impact="Medium", weight=2):
    return {"id": cid, "category": category, "name": name, "status": status,
            "finding": finding, "fix": fix, "impact": impact, "weight": weight}


def ratio_status(ok, total, good=0.9, fair=0.5):
    if total == 0:
        return "pass"
    r = ok / total
    return "pass" if r >= good else "warn" if r >= fair else "fail"


def page_check(pages, cid, category, name, predicate, finding_fmt, fix, impact="Medium",
               weight=2, applies=lambda p: True, good=0.9, fair=0.5):
    scope = [p for p in pages if applies(p)]
    if not scope:
        return None
    failing = [p for p in scope if not predicate(p)]
    ok = len(scope) - len(failing)
    finding = finding_fmt.format(ok=ok, total=len(scope))
    if failing:
        finding += " Needs work: " + ", ".join(short(p["url"]) for p in failing[:4])
        if len(failing) > 4:
            finding += f" and {len(failing) - 4} more."
    return check(cid, category, name, ratio_status(ok, len(scope), good, fair), finding, fix, impact, weight)


def short(url):
    parts = urllib.parse.urlsplit(url)
    return (parts.path or "/") + (("?" + parts.query) if parts.query else "")


def run_checks(ctx):
    pages = [p for p in ctx["pages"] if p["status"] == 200]
    checks = []
    home = ctx["home_url"]

    # 1. Access ─────────────────────────────────────────────────────────────────
    https = home.startswith("https://")
    checks.append(check("https", "access", "Served over HTTPS", "pass" if https else "fail",
                        "Site loads over HTTPS." if https else f"Homepage resolved to {home} (not HTTPS).",
                        "Serve every page over HTTPS and redirect http:// to https://.", "High", 3))

    rp, robots_status, _, _ = ctx["robots"]
    blocked_search = [b for b in SEARCH_BOTS if not rp.can_fetch(b, home)]
    blocked_training = [b for b in TRAINING_BOTS if not rp.can_fetch(b, home)]
    robots_note = "no robots.txt found (everything allowed)" if robots_status == 404 else f"robots.txt returned {robots_status}"
    checks.append(check(
        "robots_search", "access", "AI search bots allowed in robots.txt",
        "fail" if blocked_search else "pass",
        ("Blocked: " + ", ".join(blocked_search) + ". These bots fetch pages to answer questions live, "
         "so blocked pages cannot be cited.") if blocked_search else
        f"All AI search bots can fetch the homepage ({', '.join(SEARCH_BOTS)}); {robots_note}.",
        "Remove Disallow rules for OAI-SearchBot, ChatGPT-User, PerplexityBot, Claude-SearchBot, Bingbot and Googlebot.",
        "High", 5))
    checks.append(check(
        "robots_training", "access", "AI training bots allowed in robots.txt",
        "warn" if blocked_training else "pass",
        ("Blocked: " + ", ".join(blocked_training) + ". A valid business choice, but models will know less "
         "about the brand.") if blocked_training else "Training crawlers are not blocked.",
        "If brand awareness in AI models matters, allow GPTBot, ClaudeBot, Google-Extended and Applebot-Extended.",
        "Low", 1))

    ua = ctx["ua_tests"]
    blocked_ua = [b for b, r in ua.items() if r["blocked"]]
    checks.append(check(
        "firewall", "access", "Not blocked by firewall/CDN",
        "warn" if blocked_ua else "pass",
        ("Requests identifying as " + ", ".join(f"{b} (HTTP {ua[b]['status']})" for b in blocked_ua) +
         " were refused or challenged while a normal browser was served. Some CDNs block look-alike bot "
         "requests from non-official IPs, so confirm in your CDN's bot settings.") if blocked_ua else
        "Pages load normally for GPTBot, OAI-SearchBot, PerplexityBot and ClaudeBot user agents.",
        "In Cloudflare/your CDN, check 'Block AI bots', bot fight mode and WAF rules; allow verified AI search crawlers.",
        "High", 4))

    sitemap_at = ctx["sitemap_at"]
    checks.append(check(
        "sitemap", "access", "XML sitemap available", "pass" if sitemap_at else "warn",
        f"Sitemap found at {sitemap_at} ({ctx['sitemap_count']} URLs)." if sitemap_at else
        "No sitemap found in robots.txt, /sitemap.xml or /sitemap_index.xml.",
        "Publish an XML sitemap, list it in robots.txt, and submit it to Google Search Console and Bing Webmaster Tools "
        "(ChatGPT search relies heavily on Bing's index).", "Medium", 2))

    c = page_check(pages, "rendered", "access", "Content readable without JavaScript",
                   lambda p: p["words"] >= 120,
                   "{ok} of {total} pages have their main text in the raw HTML.",
                   "Most AI crawlers do not run JavaScript. Server-render or pre-render pages so the text is in the HTML.",
                   "High", 5)
    checks.append(c)

    c = page_check(ctx["pages"], "indexable", "access", "Pages load and are indexable",
                   lambda p: p["status"] == 200 and not p["noindex"],
                   "{ok} of {total} sampled pages return 200 without noindex.",
                   "Fix errors and remove noindex from pages you want cited.", "High", 4)
    checks.append(c)

    c = page_check(pages, "speed", "access", "Fast server response",
                   lambda p: p["elapsed"] < 2.0,
                   "{ok} of {total} pages responded in under 2 seconds.",
                   "Speed up server response (caching, CDN, static pages). Slow pages are fetched less reliably.",
                   "Low", 1, good=0.8)
    checks.append(c)

    llms = ctx["llms_txt"]
    checks.append(check("llms", "access", "llms.txt present (optional)", "pass" if llms else "warn",
                        "/llms.txt found." if llms else "No /llms.txt file.",
                        "Optional: add /llms.txt summarising the site. Cheap to add; adoption by major engines is not confirmed.",
                        "Low", 1))

    # 2. Content ────────────────────────────────────────────────────────────────
    checks.append(page_check(pages, "h1", "content", "One clear H1 per page",
                             lambda p: p["h1"] == 1, "{ok} of {total} pages have exactly one H1.",
                             "Give every page a single H1 that states what the page is about.", "Medium", 2))
    checks.append(page_check(pages, "title", "content", "Descriptive page titles",
                             lambda p: 10 <= len(p["title"]) <= 70,
                             "{ok} of {total} pages have a title of 10–70 characters.",
                             "Write unique titles that name the topic and brand, under ~65 characters.", "Medium", 2))
    checks.append(page_check(pages, "description", "content", "Meta descriptions",
                             lambda p: 50 <= len(p["description"]) <= 170,
                             "{ok} of {total} pages have a meta description of 50–170 characters.",
                             "Add a plain-English summary of each page (one or two sentences).", "Low", 1))
    checks.append(page_check(pages, "questions", "content", "Question-led headings",
                             lambda p: len(p["question_headings"]) >= 1,
                             "{ok} of {total} pages have at least one heading phrased as a question.",
                             "Use the questions buyers actually ask as H2/H3 headings (What is…, How much…, Which…).",
                             "High", 4, good=0.5, fair=0.2))
    checks.append(page_check(pages, "answer_first", "content", "Answer-first sections",
                             lambda p: p["answer_first_ok"] >= 0.7 * len(p["question_headings"]),
                             "{ok} of {total} pages with question headings answer them directly in the first sentence.",
                             "Start each section with a direct 1–2 sentence answer, then add detail. Engines quote the opening lines.",
                             "High", 4, applies=lambda p: p["question_headings"], good=0.8))
    checks.append(page_check(pages, "extractable", "content", "Lists or tables used",
                             lambda p: p["lists"] + p["tables"] > 0,
                             "{ok} of {total} pages use lists or tables.",
                             "Present steps, features, prices and comparisons as lists or tables; they are easy to extract.",
                             "Medium", 2, good=0.7))
    checks.append(page_check(pages, "depth", "content", "Enough substance per page",
                             lambda p: p["words"] >= 300,
                             "{ok} of {total} pages have 300+ words of text.",
                             "Thin pages rarely get cited. Add specifics: facts, numbers, examples, FAQs.",
                             "Medium", 3, good=0.7, fair=0.4))
    checks.append(page_check(pages, "alt", "content", "Image alt text",
                             lambda p: p["images_alt"] >= 0.8 * p["images"],
                             "{ok} of {total} pages with images give 80%+ of them alt text.",
                             "Describe meaningful images with alt text (alt=\"\" for decorative ones).",
                             "Low", 1, applies=lambda p: p["images"] > 0))

    # 3. Structured data ────────────────────────────────────────────────────────
    all_items = [i for p in pages for i in p["jsonld_items"]]
    invalid_pages = [p for p in pages if p["jsonld_invalid"]]
    checks.append(check("jsonld_valid", "schema", "Structured data is valid JSON",
                        "fail" if invalid_pages else "pass",
                        (f"Invalid JSON-LD on {len(invalid_pages)} page(s): " +
                         ", ".join(short(p["url"]) for p in invalid_pages[:4])) if invalid_pages else
                        "All JSON-LD blocks parse correctly.",
                        "Fix the broken JSON-LD blocks; engines ignore markup that doesn't parse. Test with validator.schema.org.",
                        "High", 3))
    checks.append(page_check(pages, "jsonld_any", "schema", "Pages carry structured data",
                             lambda p: bool(p["jsonld_types"]),
                             "{ok} of {total} pages include JSON-LD structured data.",
                             "Add JSON-LD to every page: Organization sitewide, plus Service, Product, Article or FAQPage as fits.",
                             "High", 4, good=0.8, fair=0.4))

    orgs = [i for i in all_items if is_org(i)]
    org_names = sorted({str(o.get("name")) for o in orgs if o.get("name")})
    checks.append(check("org", "schema", "Organization schema",
                        "pass" if orgs else "fail",
                        f"Organization-type markup found ({', '.join(org_names) or 'unnamed'})." if orgs else
                        "No Organization / LocalBusiness markup found.",
                        "Add Organization (or LocalBusiness) JSON-LD with name, url, logo, description, contact details and sameAs.",
                        "High", 4))

    same_as = set()
    for o in orgs:
        v = o.get("sameAs", [])
        same_as.update([v] if isinstance(v, str) else [str(x) for x in v])
    checks.append(check("sameas", "schema", "Entity links (sameAs)",
                        "pass" if len(same_as) >= 2 else "warn" if same_as else "fail",
                        f"{len(same_as)} sameAs profile link(s): " + ", ".join(sorted(same_as)[:5]) if same_as else
                        "No sameAs links connecting the organisation to its other profiles.",
                        "List official profiles in sameAs (LinkedIn, Companies House, Crunchbase, Wikipedia/Wikidata, social). "
                        "This helps engines confirm who you are.", "Medium", 3))

    has_faq = any("FAQPage" in p["jsonld_types"] for p in pages)
    any_questions = any(p["question_headings"] for p in pages)
    checks.append(check("faq", "schema", "FAQPage markup",
                        "pass" if has_faq else "warn",
                        "FAQPage markup found." if has_faq else
                        ("Question headings exist but no FAQPage markup." if any_questions else "No FAQ content or markup found."),
                        "Add an FAQ section built from real buyer questions and mark it up with FAQPage JSON-LD.",
                        "Medium", 2))
    checks.append(page_check(pages, "breadcrumb", "schema", "Breadcrumb markup on inner pages",
                             lambda p: "BreadcrumbList" in p["jsonld_types"],
                             "{ok} of {total} inner pages have BreadcrumbList markup.",
                             "Add BreadcrumbList JSON-LD to inner pages so engines understand the site hierarchy.",
                             "Low", 1, applies=lambda p: short(p["url"]) not in ("/", ""), good=0.7, fair=0.3))
    checks.append(page_check(pages, "canonical", "schema", "Canonical URLs",
                             lambda p: bool(p["canonical"]),
                             "{ok} of {total} pages declare a canonical URL.",
                             "Add <link rel=\"canonical\"> to every page to consolidate duplicates.", "Medium", 2))
    checks.append(page_check(pages, "og", "schema", "Open Graph tags",
                             lambda p: p["og"],
                             "{ok} of {total} pages have og:title and og:description.",
                             "Add Open Graph tags; many tools use them to summarise and preview pages.", "Low", 1))

    # 4. Freshness & trust ──────────────────────────────────────────────────────
    checks.append(page_check(pages, "dates", "trust", "Visible or marked-up dates",
                             lambda p: p["has_date"],
                             "{ok} of {total} pages show a published/updated date or dateModified.",
                             "Show 'Last updated' dates on content pages and include datePublished/dateModified in JSON-LD. "
                             "Engines favour fresh sources.", "Medium", 3, good=0.6, fair=0.25))
    articles = [p for p in pages if p["is_article"]]
    if articles:
        checks.append(page_check(articles, "author", "trust", "Named authors on articles",
                                 lambda p: p["article_has_author"] or p["meta_author"],
                                 "{ok} of {total} article pages name an author.",
                                 "Give articles a named author with credentials (Person schema, author bio page).",
                                 "Medium", 2))
    all_links = {short(u).lower() for p in pages for u in p["internal_links"]} | {short(p["url"]).lower() for p in pages}
    has_about = any("about" in l for l in all_links)
    has_contact = any("contact" in l for l in all_links)
    checks.append(check("about_contact", "trust", "About and contact pages",
                        "pass" if has_about and has_contact else "warn",
                        f"About page: {'found' if has_about else 'not found'}; contact page: {'found' if has_contact else 'not found'}.",
                        "Link to clear About and Contact pages that state who you are, where you are and how to reach you.",
                        "Medium", 2))
    checks.append(page_check(pages, "lang", "trust", "Language declared",
                             lambda p: bool(p["lang"]),
                             "{ok} of {total} pages set <html lang>.",
                             "Set <html lang=\"en-GB\"> (or the right language) on every page.", "Low", 1))

    return [c for c in checks if c]


def score(checks):
    by_cat, total = {}, 0.0
    for cid, name, weight in CATEGORIES:
        cs = [c for c in checks if c["category"] == cid]
        w = sum(c["weight"] for c in cs)
        s = round(100 * sum(c["weight"] * FACTOR[c["status"]] for c in cs) / w) if w else 100
        by_cat[cid] = {"name": name, "score": s, "weight": weight,
                       "counts": {k: sum(1 for c in cs if c["status"] == k) for k in FACTOR}}
        total += s * weight / 100
    return round(total), by_cat


def grade(s):
    return "A" if s >= 85 else "B" if s >= 70 else "C" if s >= 55 else "D" if s >= 40 else "E"


# ─── AI visibility ────────────────────────────────────────────────────────────

def ask_perplexity(question, key, model):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": question}]}).encode()
    req = urllib.request.Request("https://api.perplexity.ai/chat/completions", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90, context=SSL_CTX) as r:
        data = json.loads(r.read().decode("utf-8"))
    answer = data["choices"][0]["message"]["content"]
    cites = list(data.get("citations") or [])
    cites += [s.get("url") for s in data.get("search_results") or [] if s.get("url")]
    return answer, list(dict.fromkeys(cites))


def ai_visibility(questions, brand, site_host):
    key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    model = os.environ.get("PERPLEXITY_MODEL", "sonar")
    rows = []
    for q in questions:
        row = {"question": q, "links": {
            "ChatGPT": "https://chatgpt.com/?hints=search&q=" + urllib.parse.quote(q),
            "Perplexity": "https://www.perplexity.ai/search?q=" + urllib.parse.quote(q),
            "Google": "https://www.google.com/search?q=" + urllib.parse.quote(q),
            "Bing": "https://www.bing.com/search?q=" + urllib.parse.quote(q),
        }}
        if key:
            try:
                answer, cites = ask_perplexity(q, key, model)
                row["mentioned"] = brand.lower() in answer.lower() or site_host in answer.lower()
                row["cited"] = any(host_key(c) == site_host for c in cites if c.startswith("http"))
                row["competitors"] = [host_key(c) for c in cites if c.startswith("http") and host_key(c) != site_host][:6]
                row["answer"] = answer[:600]
            except Exception as e:
                row["error"] = str(e)[:200]
        rows.append(row)
    return {"automated": bool(key), "model": model if key else "", "rows": rows}


# ─── Report ───────────────────────────────────────────────────────────────────

STATUS_LABEL = {"pass": "Pass", "warn": "Improve", "fail": "Fix"}


def render_html(r):
    e = html.escape
    overall, cats = r["score"], r["categories"]
    fixes = sorted([c for c in r["checks"] if c["status"] != "pass"],
                   key=lambda c: (c["status"] != "fail", IMPACT_ORDER[c["impact"]], -c["weight"]))

    def bar(s):
        color = "var(--good)" if s >= 70 else "var(--mid)" if s >= 45 else "var(--bad)"
        return f'<div class="bar"><span style="width:{s}%;background:{color}"></span></div>'

    cat_cards = "".join(
        f'<div class="cat"><div class="cat-top"><span>{e(v["name"])}</span><strong>{v["score"]}</strong></div>{bar(v["score"])}'
        f'<p class="muted small">{v["counts"]["pass"]} pass · {v["counts"]["warn"]} improve · {v["counts"]["fail"]} fix · weight {v["weight"]}%</p></div>'
        for v in cats.values())

    fix_rows = "".join(
        f'<li><span class="pill {c["status"]}">{STATUS_LABEL[c["status"]]}</span><span class="impact">{c["impact"]} impact</span>'
        f'<h4>{e(c["name"])}</h4><p>{e(c["fix"])}</p><p class="muted small">{e(c["finding"])}</p></li>'
        for c in fixes) or "<li><p>No issues found. Focus on off-site mentions and monitoring AI answers.</p></li>"

    sections = ""
    for cid, name, _ in CATEGORIES:
        rows = "".join(
            f'<tr><td><span class="pill {c["status"]}">{STATUS_LABEL[c["status"]]}</span></td><td><strong>{e(c["name"])}</strong>'
            f'<div class="muted small">{e(c["finding"])}</div></td><td class="small">{e(c["fix"]) if c["status"] != "pass" else "—"}</td></tr>'
            for c in r["checks"] if c["category"] == cid)
        sections += (f'<h3>{e(name)} <span class="muted">— {cats[cid]["score"]}/100</span></h3>'
                     f'<table><thead><tr><th style="width:90px">Result</th><th>Check</th><th style="width:38%">How to fix</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table>')

    page_rows = ""
    for p in r["pages"]:
        issues = []
        if p["status"] != 200:
            issues.append(f"HTTP {p['status'] or p['error'][:60]}")
        else:
            if p["noindex"]: issues.append("noindex")
            if p["words"] < 120: issues.append("little text in HTML")
            if p["h1"] != 1: issues.append(f"{p['h1']} H1s")
            if not p["jsonld_types"]: issues.append("no schema")
            if p["jsonld_invalid"]: issues.append("invalid schema")
            if not p["question_headings"]: issues.append("no question headings")
            if not p["description"]: issues.append("no description")
            if not p["canonical"]: issues.append("no canonical")
        page_rows += (f'<tr><td class="url"><a href="{e(p["url"])}">{e(short(p["url"]))}</a><div class="muted small">{e(p["title"][:80])}</div></td>'
                      f'<td>{p["status"]}</td><td>{p["words"]}</td><td>{len(p["question_headings"])}</td>'
                      f'<td class="small">{e(", ".join(p["jsonld_types"][:6])) or "—"}</td>'
                      f'<td class="small">{e("; ".join(issues)) or "✓"}</td></tr>')

    vis = r["visibility"]
    if vis["rows"]:
        auto = vis["automated"]
        head = "<th>Mentioned</th><th>Cited</th><th>Other sources cited</th>" if auto else ""
        vrows = ""
        for row in vis["rows"]:
            links = " ".join(f'<a class="chip" href="{e(u)}" target="_blank" rel="noopener">{e(n)}</a>' for n, u in row["links"].items())
            cells = ""
            if auto:
                if row.get("error"):
                    cells = f'<td colspan="3" class="small muted">Error: {e(row["error"])}</td>'
                else:
                    yn = lambda b: f'<span class="pill {"pass" if b else "fail"}">{"Yes" if b else "No"}</span>'
                    cells = (f'<td>{yn(row["mentioned"])}</td><td>{yn(row["cited"])}</td>'
                             f'<td class="small">{e(", ".join(row["competitors"])) or "—"}</td>')
            vrows += f'<tr><td><strong>{e(row["question"])}</strong><div class="links">{links}</div></td>{cells}</tr>'
        if auto:
            ok = [x for x in vis["rows"] if not x.get("error")]
            m = sum(1 for x in ok if x["mentioned"]); c = sum(1 for x in ok if x["cited"])
            intro = (f'<p>Asked {len(ok)} question(s) to Perplexity ({e(vis["model"])}). '
                     f'<strong>{r["brand"]}</strong> was mentioned in <strong>{m}</strong> and the site cited in <strong>{c}</strong>. '
                     'Answers vary run to run, so track this monthly.</p>')
        else:
            intro = ('<p>Run each question in each engine and note whether you are mentioned, cited, and who is named instead. '
                     'Set <code>PERPLEXITY_API_KEY</code> to automate the Perplexity column.</p>')
        if r["questions_suggested"]:
            intro += ('<p class="note">These questions were taken from the site\'s own headings. Replace them with the real '
                      'questions buyers ask before choosing a supplier (use <code>--questions file.txt</code>).</p>')
        vis_html = f'{intro}<table><thead><tr><th>Question</th>{head}</tr></thead><tbody>{vrows}</tbody></table>'
    else:
        vis_html = ('<p>No questions to test. Create a text file with one buyer question per line and run with '
                    '<code>--questions file.txt</code>.</p>')

    ring = "var(--good)" if overall >= 70 else "var(--mid)" if overall >= 45 else "var(--bad)"
    return f"""<!DOCTYPE html>
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AEO audit — {e(r['host'])}</title>
<style>
:root{{--ink:#0D0F14;--muted:#5B5E66;--line:#E3E0D7;--paper:#F7F6F2;--surface:#fff;--accent:#FF5B2E;--good:#1E9E5A;--mid:#E0A100;--bad:#D23B1E}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:40px 20px 80px}}h1,h2,h3,h4{{margin:0;letter-spacing:-.02em}}
h2{{font-size:1.5rem;margin:48px 0 16px}}h3{{font-size:1.1rem;margin:32px 0 10px}}h4{{font-size:1rem;margin:6px 0 4px}}
p{{margin:6px 0}}a{{color:inherit}}.muted{{color:var(--muted)}}.small{{font-size:.85rem}}code{{background:#EDEAE2;padding:1px 6px;border-radius:5px}}
header.top{{display:flex;flex-wrap:wrap;gap:28px;align-items:center;justify-content:space-between;background:var(--ink);color:#fff;border-radius:22px;padding:32px}}
header.top .muted{{color:#A9ACB4}}.eyebrow{{text-transform:uppercase;letter-spacing:.12em;font-size:.72rem;color:var(--accent);font-weight:700}}
header.top h1{{font-size:2rem;margin:6px 0}}
.score{{width:150px;height:150px;border-radius:50%;display:grid;place-items:center;background:conic-gradient({ring} {overall * 3.6}deg,#2A2E38 0)}}
.score div{{width:118px;height:118px;border-radius:50%;background:var(--ink);display:grid;place-items:center;text-align:center}}
.score strong{{font-size:2.4rem;line-height:1}}.score span{{font-size:.8rem;color:#A9ACB4}}
.cats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:20px}}
.cat{{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:18px}}
.cat-top{{display:flex;justify-content:space-between;align-items:baseline;font-weight:600}}.cat-top strong{{font-size:1.6rem}}
.bar{{height:8px;background:#ECE9E1;border-radius:4px;overflow:hidden;margin:10px 0 6px}}.bar span{{display:block;height:100%}}
ol.fixes{{list-style:none;padding:0;margin:0;display:grid;gap:10px;counter-reset:f}}
ol.fixes li{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px 14px 56px;position:relative;counter-increment:f}}
ol.fixes li::before{{content:counter(f);position:absolute;left:16px;top:16px;width:26px;height:26px;border-radius:50%;background:var(--ink);color:#fff;display:grid;place-items:center;font-size:.8rem;font-weight:700}}
.pill{{display:inline-block;font-size:.72rem;font-weight:700;padding:2px 9px;border-radius:99px;text-transform:uppercase;letter-spacing:.04em}}
.pill.pass{{background:#E1F4E8;color:#146B3D}}.pill.warn{{background:#FFF1CC;color:#7A5800}}.pill.fail{{background:#FBE2DC;color:#9C2A12}}
.impact{{font-size:.75rem;color:var(--muted);margin-left:8px}}
table{{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:table}}
th,td{{text-align:left;vertical-align:top;padding:10px 12px;border-bottom:1px solid var(--line)}}th{{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);background:#FBFAF7}}
tr:last-child td{{border-bottom:0}}td.url{{word-break:break-all;max-width:320px}}
.table-scroll{{overflow-x:auto}}.chip{{display:inline-block;font-size:.75rem;border:1px solid var(--line);border-radius:99px;padding:1px 9px;margin:6px 4px 0 0;text-decoration:none}}
.chip:hover{{border-color:var(--ink)}}.note{{background:#FFF6E0;border:1px solid #F1DDA6;border-radius:10px;padding:10px 14px}}
.meta-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-top:14px}}
.meta-grid div{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
footer{{margin-top:56px;font-size:.85rem;color:var(--muted)}}
@media print{{body{{background:#fff}}.wrap{{padding:0}}header.top{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}}}
</style></head><body><div class="wrap">
<header class="top">
  <div><p class="eyebrow">AEO readiness audit</p><h1>{e(r['host'])}</h1>
  <p class="muted">{e(r['home_url'])}<br>Audited {e(r['date'])} · {len(r['pages'])} pages sampled · brand: {e(r['brand'])}</p></div>
  <div class="score"><div><div><strong>{overall}</strong><br><span>out of 100<br>grade {grade(overall)}</span></div></div></div>
</header>
<div class="cats">{cat_cards}</div>

<h2>Priority fixes</h2>
<p class="muted">Ordered by severity and impact. Work top-down.</p>
<ol class="fixes">{fix_rows}</ol>

<h2>AI answer visibility</h2>
<div class="table-scroll">{vis_html}</div>

<h2>All checks</h2>
<div class="table-scroll">{sections}</div>

<h2>Pages sampled</h2>
<div class="table-scroll"><table><thead><tr><th>Page</th><th>HTTP</th><th>Words</th><th>Q-heads</th><th>Schema types</th><th>Issues</th></tr></thead>
<tbody>{page_rows}</tbody></table></div>

<h2>What this audit can't see</h2>
<div class="meta-grid">
<div><strong>Mentions on other sites</strong><p class="small muted">AI engines lean heavily on third-party sources: reviews, directories, "best of" lists, press, Reddit. Check which sources the engines cite for your questions and work to appear on them.</p></div>
<div><strong>Search console data</strong><p class="small muted">Confirm indexing in Google Search Console and Bing Webmaster Tools, and track AI referral traffic (chatgpt.com, perplexity.ai, gemini.google.com) in GA4.</p></div>
<div><strong>IP-verified bot rules</strong><p class="small muted">The firewall test uses AI bot user agents from this machine. CDNs that verify bot IPs may treat real crawlers differently. Confirm in your CDN dashboard.</p></div>
<div><strong>Content accuracy</strong><p class="small muted">The audit checks structure, not whether the facts are right or consistent with your other profiles.</p></div>
</div>
<footer>Generated by aeo_audit.py v{VERSION}. Scores weight access {CATEGORIES[0][2]}%, content {CATEGORIES[1][2]}%, structured data {CATEGORIES[2][2]}%, freshness &amp; trust {CATEGORIES[3][2]}%.</footer>
</div></body></html>"""


# ─── Main ─────────────────────────────────────────────────────────────────────

def log(msg):
    print(msg, flush=True)


class AuditError(Exception):
    """The site could not be audited (homepage unreachable or not HTML)."""


def run_audit(url, pages=20, brand=None, questions=None, log=lambda msg: None):
    """Audit a site and return the report dict. Used by the CLI and by the
    website's AI visibility check (Lambda). `questions` is a list of strings;
    None means suggest questions from the site's own headings."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    log(f"AEO audit v{VERSION}\n  site: {url}")
    home = fetch(url)
    if home.status != 200 or not home.is_html:
        raise AuditError(f"Could not load the homepage (HTTP {home.status or '—'} {home.error}).".strip())
    home_url = normalise(home.final_url)
    site_host = host_key(home_url)
    base = f"{urllib.parse.urlsplit(home_url).scheme}://{urllib.parse.urlsplit(home_url).netloc}"

    log("  reading robots.txt and sitemap…")
    robots = read_robots(base)
    sitemap_at, sm_urls = sitemap_urls(base, robots[3])
    llms = fetch(base + "/llms.txt")
    llms_ok = llms.status == 200 and not llms.is_html and len(llms.body) > 50

    log("  testing AI crawler user agents…")
    ua_tests = {}
    browser_len = len(home.body)
    home_low = home.body[:20000].decode("utf-8", errors="ignore").lower()
    for bot, ua in BOT_UAS.items():
        r = fetch(home_url, ua=ua)
        low = r.body[:20000].decode("utf-8", errors="ignore").lower()
        challenged = any(m in low for m in CHALLENGE_MARKERS) and not any(m in home_low for m in CHALLENGE_MARKERS)
        blocked = r.status in (401, 403, 406, 429, 503) or r.status == 0 or challenged or \
            (r.status == 200 and browser_len > 5000 and len(r.body) < 0.3 * browser_len)
        ua_tests[bot] = {"status": r.status, "blocked": blocked}

    pages = max(1, pages)
    home_page = analyse_page(home, site_host)
    candidates = [u for u in sm_urls if host_key(u) == site_host]
    if len(candidates) < pages - 1:
        candidates += home_page["internal_links"]
    chosen = choose_pages(home_url, candidates, pages, site_host)
    log(f"  sampling {len(chosen)} page(s)…")

    found = {normalise(home_url): home_page}
    todo = [u for u in chosen if u not in found]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for u, resp in zip(todo, pool.map(fetch, todo)):
            if resp.status == 200 and not resp.is_html:
                continue
            found[u] = analyse_page(resp, site_host)
    # a short site may not reach the target from the homepage alone: follow one more level
    if len(found) < pages:
        more = [l for p in list(found.values()) for l in p["internal_links"]]
        extra = [u for u in choose_pages(home_url, more, pages, site_host) if u not in found]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for u, resp in zip(extra, pool.map(fetch, extra)):
                if resp.status == 200 and resp.is_html:
                    found[u] = analyse_page(resp, site_host)
    page_list = list(found.values())[:pages]

    ctx = {"home_url": home_url, "robots": robots, "sitemap_at": sitemap_at,
           "sitemap_count": len(sm_urls), "llms_txt": llms_ok, "ua_tests": ua_tests, "pages": page_list}
    checks = run_checks(ctx)
    overall, cats = score(checks)

    if not brand:
        orgs = [i for p in page_list for i in p["jsonld_items"] if is_org(i) and i.get("name")]
        m = re.search(r'property="og:site_name"\s+content="([^"]+)"', home.text)
        og_site = html.unescape(m.group(1)) if m else ""
        brand = (str(orgs[0]["name"]) if orgs else og_site) or site_host.split(".")[0].title()

    suggested = questions is None
    if questions is None:
        questions = list(dict.fromkeys(q for p in page_list for q in p["question_headings"] if words(q) >= 3))[:10]
        suggested = bool(questions)
    if questions:
        log(f"  checking AI visibility for {len(questions)} question(s)"
            + (" via Perplexity API…" if os.environ.get("PERPLEXITY_API_KEY") else " (manual links)…"))
    visibility = ai_visibility(questions, brand, site_host)

    for p in page_list:
        p.pop("jsonld_items", None)
    return {
        "tool": f"aeo_audit.py v{VERSION}", "date": datetime.datetime.now().strftime("%d %b %Y %H:%M"),
        "home_url": home_url, "host": site_host, "brand": brand, "score": overall, "grade": grade(overall),
        "categories": cats, "checks": checks, "pages": page_list, "visibility": visibility,
        "questions_suggested": suggested,
    }


def top_fixes(report, n=5):
    """The most important non-passing checks, in the order the report lists them."""
    return sorted([c for c in report["checks"] if c["status"] != "pass"],
                  key=lambda c: (c["status"] != "fail", IMPACT_ORDER[c["impact"]], -c["weight"]))[:n]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Audit a website's readiness for AI answer engines (AEO).")
    ap.add_argument("url", help="site to audit, e.g. https://www.example.com")
    ap.add_argument("--pages", type=int, default=20, help="how many pages to sample (default 20)")
    ap.add_argument("--brand", help="brand name to look for in AI answers (default: detected)")
    ap.add_argument("--questions", help="text file with one buyer question per line")
    ap.add_argument("--out", default="reports", help="folder for the report (default: reports)")
    ap.add_argument("--no-open", action="store_true", help="don't open the report when done")
    args = ap.parse_args()

    questions = None
    if args.questions:
        with open(args.questions, encoding="utf-8") as f:
            questions = [clean(l) for l in f if clean(l) and not l.startswith("#")]

    try:
        report = run_audit(args.url, args.pages, args.brand, questions, log=log)
    except AuditError as e:
        log(f"  {e}")
        return 1

    os.makedirs(args.out, exist_ok=True)
    stem = f"aeo-report-{re.sub(r'[^a-z0-9.-]+', '-', report['host'])}-{datetime.date.today().isoformat()}"
    html_path = os.path.abspath(os.path.join(args.out, stem + ".html"))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_html(report))
    with open(os.path.join(args.out, stem + ".json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    log(f"\n  Score: {report['score']}/100 (grade {report['grade']})")
    for v in report["categories"].values():
        log(f"    {v['name']:<28} {v['score']:>3}")
    fails = [c for c in top_fixes(report) if c["status"] == "fail"]
    if fails:
        log("  Top fixes:")
        for c in fails:
            log(f"    - {c['name']}")
    log(f"\n  Report: {html_path}")
    if not args.no_open:
        webbrowser.open("file:///" + html_path.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
