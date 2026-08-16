#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site_crawl.py - small multi-page crawler built on web_extract.py.

Adds three things to the single-page fetcher and nothing else: a queue, a domain
boundary, and a politeness delay.

Three rules it enforces:
  1. robots.txt is read and obeyed (--ignore-robots is an explicit caller decision).
  2. Default one request per second per host.
  3. Links outside the target domain are never followed.

Honesty rule: HTTP 200 is not success. A page that returns 200 but yields less text
than --min-chars is recorded as *failed* with reason "empty shell", not as a win.
Single-page applications routinely return 200 with no readable content, and a
crawler that counts those as successes reports a harvest that never happened.

A note on robots.txt parsing: the standard library's RobotFileParser treats a rule
like `Disallow: /path/?` as if it were `Disallow: /path/`, blocking an entire branch
when the site owner only meant to exclude query-string URLs. The `Robots` class below
separates those rules out and applies them only to URLs that actually carry a query.
This was found on a real site whose gallery section was wrongly reported as
off-limits, and it fails in the dangerous direction: the crawler returns nothing and
looks well-behaved while doing it.

Usage:
  python3 site_crawl.py <start-url> --out out.json
        [--depth 2] [--max-pages 100] [--delay 1.0] [--include-pat RE]
        [--min-chars 500] [--ignore-robots] [--no-sitemap]
Exit: 0 ok (even with failed pages) - 1 nothing fetched - 2 bad input.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import deque
from urllib import robotparser
from urllib.parse import urljoin, urldefrag, urlparse, urlunparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import web_extract as W  # noqa: E402  (single source of fetch+clean; no second implementation)

TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref|source)", re.I)
SKIP_EXT = (".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
            ".mp4", ".mp3", ".woff", ".woff2", ".ttf", ".ico", ".css", ".js")


def canon(url):
    url, _ = urldefrag(url)
    p = urlparse(url)
    q = "&".join(sorted(x for x in p.query.split("&")
                        if x and not TRACKING.match(x.split("=")[0])))
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc.lower(), path, "", q, ""))


def same_site(url, root):
    a, b = urlparse(url).netloc.lower(), urlparse(root).netloc.lower()
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b)


class Robots:

    def __init__(self, text):
        kept, self.query_only = [], []
        for ln in text.splitlines():
            m = re.match(r"\s*disallow\s*:\s*(\S+?)\?\s*$", ln, re.I)
            if m:
                self.query_only.append(m.group(1))
                continue                                     # hide it from the stdlib parser
            kept.append(ln)
        self.rp = robotparser.RobotFileParser()
        self.rp.parse(kept)

    def can_fetch(self, ua, url):
        p = urlparse(url)
        if p.query and any(p.path.startswith(pre) for pre in self.query_only):
            return False
        return self.rp.can_fetch(ua, url)


def load_robots(root):
    base = f"{urlparse(root).scheme}://{urlparse(root).netloc}"
    try:
        status, headers, raw, _t, _n = W.fetch(base + "/robots.txt", 20, 200_000)
        if status != 200:
            return None, f"robots.txt returned {status}"
        text, _ = W.decode(raw, headers)
        r = Robots(text)
        note = "read"
        if r.query_only:
            note += f" ({len(r.query_only)} query-only rules corrected)"
        return r, note
    except Exception as e:                                   # noqa: BLE001
        return None, f"robots.txt unavailable ({type(e).__name__})"


def sitemap_urls(root, cap):
    base = f"{urlparse(root).scheme}://{urlparse(root).netloc}"
    try:
        status, headers, raw, _t, _n = W.fetch(base + "/sitemap.xml", 25, 3_000_000)
        if status != 200:
            return []
        text, _ = W.decode(raw, headers)
        return [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)][:cap]
    except Exception:                                        # noqa: BLE001
        return []


def crawl(a):
    root = a.url
    rp, robots_note = (None, "ignored (--ignore-robots)") if a.ignore_robots \
        else load_robots(root)
    include = re.compile(a.include_pat) if a.include_pat else None

    seeds = [] if a.no_sitemap else sitemap_urls(root, a.max_pages)
    queue = deque([(root, 0)] + [(u, 1) for u in seeds if same_site(u, root)])
    seen, pages, failed = {canon(root)}, [], []
    last_hit = 0.0

    while queue and len(pages) < a.max_pages:
        url, depth = queue.popleft()
        gap = a.delay - (time.monotonic() - last_hit)
        if gap > 0:
            time.sleep(gap)                                  # politeness delay
        last_hit = time.monotonic()

        if rp is not None and not rp.can_fetch(W.UA, url):
            failed.append({"url": url, "reason": "disallowed by robots.txt"})
            continue
        try:
            status, headers, raw, trunc, size = W.fetch(url, a.timeout, a.max_bytes)
        except Exception as e:                               # noqa: BLE001
            failed.append({"url": url, "reason": f"network: {type(e).__name__}"})
            continue

        html, charset = W.decode(raw, headers)
        p = W.Reader(url, False, main_only=True)
        p.feed(html)
        body = p.text()

        if len(body) < a.min_chars:
            # 200 with negligible text = shell. Not counted as success.
            failed.append({"url": url, "status": status, "text_chars": len(body),
                           "reason": f"empty shell (under {a.min_chars} chars) - probably client-rendered"})
        else:
            pages.append({"url": url, "status": status, "title": p.title,
                          "charset": charset, "depth": depth, "text": body,
                          "jsonld": p.jsonld, "meta": p.meta,
                          "text_chars": len(body), "truncated": trunc})

        if depth >= a.depth:
            continue
        for link in p.links:
            c = canon(link)
            if c in seen or not same_site(link, root):
                continue
            if urlparse(c).path.lower().endswith(SKIP_EXT):
                continue
            if include and not include.search(c):
                continue
            seen.add(c)
            queue.append((link, depth + 1))

    return {
        "root": root,
        "robots": robots_note,
        "sitemap_seeds": len(seeds),
        "settings": {"depth": a.depth, "max_pages": a.max_pages,
                     "delay": a.delay, "min_chars": a.min_chars,
                     "include_pat": a.include_pat or None},
        "pages": pages,
        "failed": failed,
        # Four mandatory honesty counters - a report without them is incomplete.
        "summary": {"queued_total": len(seen), "ok": len(pages), "failed": len(failed),
                    "text_chars_total": sum(x["text_chars"] for x in pages),
                    "stopped_at_cap": len(pages) >= a.max_pages},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default="")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--max-bytes", type=int, default=W.DEFAULT_MAX)
    ap.add_argument("--min-chars", type=int, default=500)
    ap.add_argument("--include-pat", default="")
    ap.add_argument("--ignore-robots", action="store_true")
    ap.add_argument("--no-sitemap", action="store_true")
    a = ap.parse_args()

    if urlparse(a.url).scheme not in ("http", "https"):
        print("::error::URL must start with http or https.", file=sys.stderr)
        return 2

    doc = crawl(a)
    s = doc["summary"]
    if a.out:
        json.dump(doc, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    else:
        print(json.dumps(doc, ensure_ascii=False, indent=1))

    print(f"- {doc['root']}", file=sys.stderr)
    print(f"  robots: {doc['robots']} - sitemap seeds: {doc['sitemap_seeds']}", file=sys.stderr)
    print(f"  queued {s['queued_total']} - ok {s['ok']} - failed {s['failed']} - "
          f"text {s['text_chars_total']} chars", file=sys.stderr)
    if s["stopped_at_cap"]:
        print(f"  ! stopped at the {a.max_pages}-page cap - the rest was not crawled.", file=sys.stderr)
    for f in doc["failed"][:10]:
        print(f"  x {f['url']} - {f['reason']}", file=sys.stderr)
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
