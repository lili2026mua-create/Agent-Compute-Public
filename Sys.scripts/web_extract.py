#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web_extract.py - fetch a web page, strip the chrome, emit readable text.

Standard library only. No pip install, no third-party parser. That is deliberate:
the same file has to run unchanged on a laptop, in CI, and on constrained devices.

What it produces: readable text, JSON-LD blocks, meta tags, links, detected charset.
Check JSON-LD first - product prices, ratings and events usually live there, and
reading it is cheaper and more accurate than parsing prose.

Two bugs worth knowing about, both fixed here and both found on real pages:

  * Non-ASCII URLs crash. `http.client` encodes the request line as ASCII, so a URL
    with non-Latin characters raises UnicodeEncodeError before any request is sent.
    `normalize()` applies IDNA to the host and percent-encoding to path and query.

  * Inline elements run together. Without a soft space between adjacent inline
    elements, a tag list renders as one unreadable word.

Truncation is never silent: when the body is cut at the byte cap, `stats.truncated`
and the real size are reported. A caller that cannot see the cut will trust a
partial page as a whole one.

Usage:
  python3 web_extract.py <url> [--format json|md|text] [--main] [--max-bytes N]
        [--timeout S] [--out FILE] [--keep-links] [--raw-html FILE]
Exit: 0 ok - 1 network error or blocked host - 2 bad input.
"""
import argparse
import gzip
import json
import re
import sys
import zlib
from html.parser import HTMLParser
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse, urlunparse

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
DROP = {"script", "style", "noscript", "svg", "canvas", "template", "iframe"}
BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside",
         "br", "hr", "tr", "li", "ul", "ol", "table", "blockquote", "pre",
         "h1", "h2", "h3", "h4", "h5", "h6", "form", "nav", "figure"}
HEAD = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
# --main mode: drop the page chrome and keep the main content.
CHROME_TAGS = {"nav", "header", "footer", "aside"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
ROOTISH = {"html", "body", "main", "article"}          # never chrome
HINTABLE = {"div", "section", "ul", "ol", "span", "form", "table", "figure", "p"}
CHROME_HINT = re.compile(
    r"(^|[\s_-])(nav|menu|sidebar|side-?bar|footer|header|banner|breadcrumb|cookie|"
    r"consent|advert|ads?|promo|social|share|subscribe|newsletter|related|comment|"
    r"pagination|toc|skip)([\s_-]|$)", re.I)

DEFAULT_MAX = 3_000_000          # download cap


class Reader(HTMLParser):

    def __init__(self, base_url, keep_links, main_only=False):
        super().__init__(convert_charrefs=True)
        self.base, self.keep_links, self.main_only = base_url, keep_links, main_only
        self._chrome = 0          # nesting depth inside a skipped chrome region
        self.dropped_chrome = 0   # counted so the removal is never silent
        self.out, self.meta, self.jsonld, self.links = [], {}, [], []
        self.title, self._skip, self._ld, self._in_title = "", 0, None, False
        self._href = None

    def _push(self, s):
        if s:
            self.out.append(s)

    def _space(self):
        if self.out and not self.out[-1].endswith((" ", "\n", "[")):
            self.out.append(" ")

    def _break(self, tag):
        if self.out and self.out[-1] != "\n":
            self.out.append("\n")
        if tag in ("p", "div", "section", "article", "table", "pre") or tag in HEAD:
            if len(self.out) < 2 or self.out[-2] != "\n":
                self.out.append("\n")

    def _is_chrome(self, tag, a):
        if tag in ROOTISH:
            return False
        if tag in CHROME_TAGS:
            return True
        if a.get("role", "").lower() in ("navigation", "banner", "contentinfo", "search"):
            return True
        if tag not in HINTABLE:
            return False
        ident = f"{a.get('id','')} {a.get('class','')}"
        return bool(ident.strip()) and bool(CHROME_HINT.search(ident))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self._chrome:
            if tag not in VOID:
                self._chrome += 1
            return
        if self.main_only and self._is_chrome(tag, a):
            self._chrome = 1
            self.dropped_chrome += 1
            return
        if tag in DROP:
            if tag == "script" and a.get("type", "").lower() == "application/ld+json":
                self._ld = []          # JSON-LD is data, not script - keep it
            else:
                self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            k = (a.get("property") or a.get("name") or "").strip().lower()
            v = (a.get("content") or "").strip()
            if k and v and k in ("description", "og:title", "og:description",
                                 "og:site_name", "og:type", "author", "keywords"):
                self.meta.setdefault(k, v)
        elif tag == "a" and a.get("href"):
            href = urljoin(self.base, a["href"].strip())
            if href.startswith(("http://", "https://")):
                self.links.append(href)
                if self.keep_links:
                    self._href = href
        elif tag in HEAD:
            self._break(tag)
            self._push(HEAD[tag] + " ")
            return
        elif tag == "li":
            self._break(tag)
            self._push("- ")
            return
        elif tag in ("td", "th"):
            self._push(" | ")
            return
        if tag in BLOCK:
            self._break(tag)
        else:
            self._space()

    def handle_endtag(self, tag):
        if self._chrome:
            self._chrome -= 1
            return
        if tag in DROP:
            if tag == "script" and self._ld is not None:
                blob = "".join(self._ld).strip()
                self._ld = None
                try:
                    self.jsonld.append(json.loads(blob))
                except Exception:
                    pass               # malformed JSON-LD is skipped without breaking the text
            elif self._skip:
                self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._href:
            self._push(f"]({self._href})")
            self._href = None
        elif tag in BLOCK:
            self._break(tag)
        else:
            self._space()

    def handle_data(self, data):
        if self._chrome:
            return
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.title += data.strip()
            return
        txt = re.sub(r"[ \t\r\f\v]+", " ", data)
        if txt.strip():
            if self._href and not (self.out and self.out[-1].endswith("[")):
                self._push("[")
            self._push(txt)

    def text(self):
        s = "".join(self.out)
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r" +([,.;:!?)\]،؛؟])", r"\1", s)
        s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()


def normalize(url):
    p = urlparse(url)
    try:
        host = p.hostname.encode("idna").decode("ascii") if p.hostname else ""
    except (UnicodeError, AttributeError):
        host = p.hostname or ""
    netloc = host
    if p.port:
        netloc += f":{p.port}"
    if p.username:
        netloc = f"{p.username}@{netloc}"
    return urlunparse((
        p.scheme, netloc,
        quote(p.path, safe="/%:@&=+$,~"),
        quote(p.params, safe="/%:@&=+$,~"),
        quote(p.query, safe="/%:@&=+$,~?"),
        quote(p.fragment, safe="/%:@&=+$,~"),
    ))


def fetch(url, timeout, max_bytes):
    req = urlrequest.Request(normalize(url), headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fa,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    with urlrequest.urlopen(req, timeout=timeout) as r:
        raw = r.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        enc = (r.headers.get("Content-Encoding") or "").lower()
        if enc == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass                   # a truncated body is not valid gzip - keep it raw
        elif enc == "deflate":
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                pass
        return r.status, dict(r.headers), raw, truncated, len(raw)


def decode(raw, headers):
    ct = (headers.get("Content-Type") or "").lower()
    m = re.search(r"charset=([\w\-]+)", ct)
    cands = [m.group(1)] if m else []
    m2 = re.search(rb'charset=["\']?([\w\-]+)', raw[:4096], re.I)
    if m2:
        cands.append(m2.group(1).decode("ascii", "ignore"))
    cands += ["utf-8", "windows-1256", "iso-8859-1"]
    for c in cands:
        try:
            return raw.decode(c), c
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8/replace"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--format", choices=("json", "md", "text"), default="json")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--out", default="")
    ap.add_argument("--keep-links", action="store_true",
                    help="keep links inline as markdown (default: list only)")
    ap.add_argument("--main", action="store_true",
                    help="drop nav, sidebar, header and footer; keep the main content")
    ap.add_argument("--raw-html", default="", help="also write the raw body to this file")
    a = ap.parse_args()

    if not urlparse(a.url).scheme in ("http", "https"):
        print("::error::URL must start with http or https.", file=sys.stderr)
        return 2

    try:
        status, headers, raw, truncated, size = fetch(a.url, a.timeout, a.max_bytes)
    except HTTPError as e:
        print(f"::error::host returned {e.code} - {a.url}", file=sys.stderr)
        return 1
    except URLError as e:
        print(f"::error::network unreachable ({e.reason}) - {a.url}\n"
              "A connection reset usually means egress to this host is filtered; "
              "try running from a network without that restriction.", file=sys.stderr)
        return 1

    html, charset = decode(raw, headers)
    if a.raw_html:
        open(a.raw_html, "w", encoding="utf-8").write(html)

    p = Reader(a.url, a.keep_links, main_only=a.main)
    p.feed(html)
    body = p.text()

    seen, links = set(), []
    for u in p.links:
        if u not in seen:
            seen.add(u)
            links.append(u)

    doc = {
        "url": a.url,
        "status": status,
        "charset": charset,
        "title": p.title,
        "meta": p.meta,
        "jsonld": p.jsonld,
        "text": body,
        "links": links[:200],
        "stats": {
            "html_bytes": size,
            "text_chars": len(body),
            "links_total": len(links),
            "links_shown": min(len(links), 200),
            "jsonld_blocks": len(p.jsonld),
            "truncated": truncated,
            "chrome_blocks_dropped": p.dropped_chrome,
        },
    }
    if truncated:
        doc["stats"]["truncation_note"] = (
            f"body truncated at the {a.max_bytes}-byte cap - raise --max-bytes.")
    if len(links) > 200:
        doc["stats"]["links_note"] = f"showing the first 200 of {len(links)} links."

    if a.format == "json":
        out = json.dumps(doc, ensure_ascii=False, indent=1)
    elif a.format == "text":
        out = body
    else:
        head = [f"# {p.title}" if p.title else "", f"> source: {a.url}"]
        for k, v in p.meta.items():
            head.append(f"> {k}: {v}")
        if p.jsonld:
            head.append("\n## Structured data (JSON-LD)\n")
            head.append("```json\n" + json.dumps(p.jsonld, ensure_ascii=False, indent=1) + "\n```")
        head.append("\n## Page text\n")
        out = "\n".join(x for x in head if x) + "\n" + body
        if truncated:
            out += f"\n\n> ! body truncated at the {a.max_bytes}-byte cap."

    if a.out:
        open(a.out, "w", encoding="utf-8").write(out)
        s = doc["stats"]
        print(f"✓ {a.url} → {a.out} · {s['text_chars']} chars - "
              f"{s['jsonld_blocks']} JSON-LD blocks - {s['links_total']} links"
              + (" - truncated" if truncated else ""))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
