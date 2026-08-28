# Agent-Compute-Public

مخزنِ **اجرای سنگین** — فقط پردازش، بدونِ هیچ راز و بدونِ هیچ داده‌ی ماندگار.

## این مخزن چه می‌کند
گامِ ۲ی چرخهٔ محتوا (برشِ فریم و رونویسیِ صوت) این‌جا روی رانرِ عمومیِ رایگانِ گیت‌هاب اجرا
می‌شود. مخزنِ خصوصی کار را شروع و تمام می‌کند؛ این‌جا فقط پردازشِ پرهزینه انجام می‌شود.

## قواعدِ امنیتیِ تخلف‌ناپذیر
- ⛔ هیچ سکرتی در این مخزن ساخته نمی‌شود. نه توکنِ Drive، نه توکنِ GitHub، هیچ.
- ⛔ هیچ توکنی از مخزنِ خصوصی به این‌جا داده نمی‌شود. جریانِ داده یک‌طرفه است.
- ⛔ مجوزِ ورک‌فلو فقط `contents: read` است — این مخزن هیچ‌جا نمی‌نویسد.
- ⛔ رسانه با یک لینکِ **موقت** می‌آید که سمتِ خصوصی پس از پایانِ کار باطلش می‌کند.
- ⛔ لینکِ ورودی در هیچ لاگی چاپ نمی‌شود.
- خروجی فقط آرتیفکتِ یک‌روزه است؛ هیچ محتوایی در این مخزن کامیت نمی‌شود.

## اجرا
ورک‌فلوی `enrich.yml` با ورودی‌های `key`، `media_url`، `source` و `whisper_model` صدا زده
می‌شود و خروجی را با نامِ `enriched-<key>` به‌عنوانِ آرتیفکت می‌گذارد.

---

## Web harvesting tools (added 1405-05-26)

Three dependency-light tools for reading public web pages, plus two workflows that
expose them as manually-dispatched jobs returning build artifacts.

| file | what it does | needs |
|---|---|---|
| `Sys.scripts/web_extract.py` | one page → readable text + JSON-LD + links | standard library only |
| `Sys.scripts/site_crawl.py` | multi-page crawl with robots.txt and a politeness delay | standard library only |
| `Sys.scripts/render_fetch.py` | render a client-side page and read the visible text | Playwright + Chromium |
| `Sys.scripts/digikala_render.py` | render a product search, then read the page's own JSON API for listings, details and comments | Playwright + Chromium |

Workflows: `harvest.yml` (crawl), `render.yml` (browser render) and
`digikala.yml` (product search).

`digikala.yml` takes the whole search as a JSON `request` input — queries, keyword
filters and price bounds. Nothing about who asked, or why, is stored in this repo;
the caller passes it at dispatch time and reads the answer back as an artifact.

**Design rules, same spirit as the security rules above:**

- HTTP 200 is not success. A page returning 200 with almost no extractable text is
  reported as failed with the reason "empty shell", never counted as a win.
- Truncation is never silent — byte caps and link caps are always reported.
- robots.txt is obeyed, with one correction: the standard library treats
  `Disallow: /path/?` as blocking the whole branch when the site owner only meant
  query-string URLs. That rule is applied only to URLs carrying a query.
- No secrets, no stored data. Every input arrives through workflow inputs; output
  leaves as a short-retention artifact.
