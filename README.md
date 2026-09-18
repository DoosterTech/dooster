# Dooster — website

Static website for [dooster.io](https://www.dooster.io), a UK technology-led
digital studio: websites, digital marketing and AI (including AI search
visibility, also called AEO/GEO). Built from JSON and markdown content by a small Flask app
and deployed to AWS Amplify as static HTML. It uses the same setup as the
Kingfisher House site.

**All site text lives in `content/`.** Nothing editorial is hard-coded in the
templates, so copy changes never require touching HTML.

---

## Editing content

| To change… | Edit |
|---|---|
| Home page wording | `content/pages/home.json` |
| Services, Results, About, AI visibility check, Contact, 404 wording | `content/pages/<page>.json` |
| Nav, footer, email, site URL, SEO description | `content/site.json` |
| The six service pages (`/services/<slug>`) | `content/services.json` |
| Case studies (home, `/results`, `/about`) | `content/case-studies.json` |
| Organisation profile links (LinkedIn, Companies House…) | `content/site.json` → `seo.same_as` |
| Privacy and cookies policies | `content/legal/*.md` |

A new service added to `services.json` gets its own page and appears in the nav
menu, footer, sitemap and `llms.txt` automatically. Change a service's
`"updated"` date when you edit it: it shows as "Last updated" on the page and
as `dateModified` in the structured data.

### Check your changes before pushing

```bash
python validate_content.py
```

This also runs on every deploy. If it fails, **the site is not updated** and
the version already live stays live.

---

## Running locally

```bash
pip install -r requirements.txt
python app.py            # http://localhost:4051
```

## Building the static site

```bash
python export_static.py                        # relative links, opens from the filesystem (+ shareable zip)
python export_static.py --absolute --out dist  # what Amplify runs
```

The build runs three checks in order and stops at the first failure:

1. **`validate_content.py`**: content files are well formed and internal links
   point at real pages.
2. **Render**: every route is rendered through the real Flask app.
3. **`smoke_test.py`**: the output is sound. Every page is present with SEO tags,
   one `<h1>` and valid JSON-LD, and every link and asset resolves.

---

## AEO built in

The site follows its own advice:

- JSON-LD on every page: `ProfessionalService` + `WebSite` (home),
  `Service` + `FAQPage` + `BreadcrumbList` (service pages)
- Each service page opens with a short, quotable definition (for example,
  "What is AEO?")
- `/llms.txt`: a plain-text summary for LLMs, generated from the same content
- `robots.txt` (production) explicitly allows GPTBot, PerplexityBot, ClaudeBot,
  Google-Extended and other AI crawlers

---

## Deployment

**AWS Amplify**, one app with two branches, both built by `amplify.yml`.

| Branch | Builds as | Search engines |
|---|---|---|
| `main` | production | indexable |
| `dev` | dev | `noindex`, and `robots.txt` blocks all crawlers |

Work lands on `dev` and gets checked on the dev URL. It reaches `main` through a
pull request, and merging deploys the live site.

The environment comes from Amplify's `AWS_BRANCH` (see `site_env.py`), so
nothing needs setting for it. `main` refuses to build as dev.

To check a production build locally:

```bash
SITE_ENV=production python export_static.py --absolute --out dist
```

Set per branch in the Amplify console:

| Variable | `main` | `dev` |
|---|---|---|
| `FORM_ENDPOINT` | prod stack's `FormEndpoint` | dev stack's `FormEndpoint` |
| `AUDIT_ENDPOINT` | prod stack's `AuditEndpoint` | dev stack's `AuditEndpoint` |

Easier than per-branch overrides: add these four for **all branches**, and
each build picks the pair for its environment:

| Variable | Value |
|---|---|
| `FORM_ENDPOINT_DEV` / `AUDIT_ENDPOINT_DEV` | dev stack's `FormEndpoint` / `AuditEndpoint` |
| `FORM_ENDPOINT_PROD` / `AUDIT_ENDPOINT_PROD` | prod stack's `FormEndpoint` / `AuditEndpoint` |

Paste `amplify-redirects.json` into the app's **Rewrites and redirects** JSON
editor (custom 404 page). Add any future 301s to `REDIRECTS` in `app.py` and to
that file; the build fails if the two disagree.

### Contact form and free AI visibility check

Both run on one API Gateway, defined in `lambda/template.yaml` (AWS SAM):

- `POST /contact` → `contact_handler.py` → SES email to the Dooster inbox
- `POST /audit` → `audit_handler.py`: validates the website (public addresses
  only) and email, then re-invokes itself asynchronously and returns 202. The
  async run audits up to 10 pages with `aeo_audit.py`, emails the visitor a
  summary with the full HTML report attached, and emails the Dooster inbox
  with the lead and score. Throttled to 1 request/second.

`lambda/aeo_audit.py` is a copy of the standalone tool in `../aeo-audit`. Copy
it across when the tool changes.

Deploy:

```bash
cd lambda
sam build
sam deploy --config-env dev
sam deploy --config-env prod
```

Replace the `REPLACE-*` values in `lambda/samconfig.toml` first, verify the
`dooster.io` domain in SES, and request SES production access before launch.
The audit emails need `ses:SendRawEmail` (included) because the report is an
attachment. Until `FORM_ENDPOINT` / `AUDIT_ENDPOINT` are set, each form tells
visitors to email instead.

---

## Layout

```
app.py                  Flask app: routes and content loading
export_static.py        Renders every route to static HTML
validate_content.py     Content checks (runs before build)
smoke_test.py           Build checks (runs after build)
make_images.py          Regenerates logo, favicon and social preview image
site_env.py             production / dev from AWS_BRANCH
amplify.yml             AWS Amplify build spec

content/                All site text
templates/              Jinja templates (structure only, no copy)
static/                 CSS (hand-written, no build step), JS, images
lambda/                 Contact form + AI visibility check: handlers + SAM template
```

## Not yet included

- **Blog.** Deliberately left out for now. To add it, copy the blog pattern from
  the Kingfisher House repo (`content/blog/*.md`, `load_posts()`, the blog
  templates and the matching checks in the validate and smoke tests).
