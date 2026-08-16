#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_fetch.py - render a JavaScript page in a real browser and read its text.

Some pages build their content client-side. A raw-HTML fetcher sees an empty shell:
HTTP 200, a few kilobytes of scaffolding, no readable content. This script opens the
same URL in Chromium, waits for the page to settle, and takes the text a human would
actually see.

Requires Playwright and a Chromium build. It is meant to run in CI, where a browser
can reach the network freely; sandboxes that proxy all egress often block the browser
even when plain HTTP clients work.

Rendering alone is not success. If the extracted text is still below --min-chars the
page is reported as a shell and the exit code is non-zero, so a caller cannot mistake
"the browser opened" for "we got the content".

For infinite-scroll listings, raise --scroll; each pass gives the page time to load
the next batch.

Usage:
  python3 render_fetch.py <url> [--out F] [--wait networkidle|load]
        [--scroll N] [--timeout MS] [--exe PATH] [--shot PNG] [--min-chars N]
Exit: 0 real text - 1 unreachable or still a shell - 2 bad input.
"""
import argparse
import json
import os
import sys
from urllib.parse import urlparse

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def find_exe(given):
    if given:
        return given
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if base and os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            cand = os.path.join(base, d, "chrome-linux", "chrome")
            if os.path.exists(cand):
                return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default="")
    ap.add_argument("--wait", default="networkidle", choices=("load", "domcontentloaded", "networkidle"))
    ap.add_argument("--scroll", type=int, default=0,
                    help="scroll to the bottom this many times (for infinite lists)")
    ap.add_argument("--timeout", type=int, default=60000)
    ap.add_argument("--exe", default="")
    ap.add_argument("--shot", default="", help="write a full-page screenshot here")
    ap.add_argument("--min-chars", type=int, default=500)
    a = ap.parse_args()

    if urlparse(a.url).scheme not in ("http", "https"):
        print("::error::URL must start with http or https.", file=sys.stderr)
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("::error::playwright is not installed; this script is meant to run in CI.",
              file=sys.stderr)
        return 2

    exe = find_exe(a.exe)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    launch = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if exe:
        launch["executable_path"] = exe
    if proxy:
        # Pass the proxy through when one is configured; report failures honestly.
        launch["proxy"] = {"server": proxy}

    with sync_playwright() as p:
        b = p.chromium.launch(**launch)
        ctx = b.new_context(user_agent=UA, locale="fa-IR",
                            viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        try:
            pg.goto(a.url, wait_until=a.wait, timeout=a.timeout)
        except Exception as e:                                # noqa: BLE001
            print(f"::error::{a.url} did not load - {str(e).splitlines()[0][:120]}", file=sys.stderr)
            b.close()
            return 1

        for _ in range(a.scroll):
            pg.mouse.wheel(0, 20000)
            pg.wait_for_timeout(1200)                         # give the page time to load the next batch

        text = pg.inner_text("body")
        links = sorted(set(pg.eval_on_selector_all("a[href]", "e=>e.map(x=>x.href)")))
        links = [l for l in links if l.startswith(("http://", "https://"))]
        title = pg.title()
        if a.shot:
            pg.screenshot(path=a.shot, full_page=True)
        b.close()

    doc = {"url": a.url, "title": title, "text": text,
           "links": links[:400],
           "stats": {"text_chars": len(text), "links_total": len(links),
                     "links_shown": min(len(links), 400),
                     "scrolled": a.scroll, "wait": a.wait,
                     "shell": len(text) < a.min_chars}}
    if len(links) > 400:
        doc["stats"]["links_note"] = f"showing the first 400 of {len(links)} links."

    out = json.dumps(doc, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(out)
        print(f"✓ {a.url} → {a.out} · {len(text)} chars - {len(links)} links"
              + (" - still a shell" if doc["stats"]["shell"] else ""))
    else:
        print(out)
    # Rendering alone is not success - real text must have come out.
    return 1 if doc["stats"]["shell"] else 0


if __name__ == "__main__":
    sys.exit(main())
