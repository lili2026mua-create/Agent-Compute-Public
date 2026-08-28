#!/usr/bin/env python3
"""Render a JS-driven product search and extract listings, details and comments.

Why a browser: the site ships an empty Next.js shell, and its JSON API answers a
raw request with a self-referencing 307 (an edge-WAF cookie challenge). A fetch
issued from inside a loaded page already carries the solved challenge.

Config arrives at runtime, never from a committed file: set REQUEST to a JSON
object, or pass --config PATH. Nothing about who asked, or why, belongs here.

Output: out/render-*.json - raw, uninterpreted.
"""
import argparse, json, os, re, time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

OUT = os.environ.get("OUT_DIR", "out")
os.makedirs(OUT, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

HOST = "www.digikala.com"
API_HOST = "api.digikala.com"

JS_FETCH = """
async (url) => {
  const r = await fetch(url, {
    headers: {'Accept': 'application/json', 'Accept-Language': 'fa-IR,fa;q=0.9'},
    credentials: 'include',
  });
  const t = await r.text();
  try { return {status: r.status, json: JSON.parse(t)}; }
  catch (e) { return {status: r.status, text: t.slice(0, 500)}; }
}
"""

DEFAULT_CFG = {
    "queries": [],
    "pages": [],
    "must_have": [],
    "must_not": [],
    "min_price": 0,
    "max_price": 10 ** 12,
    "max_pages": 3,
    "top_n": 12,
}


def load_cfg(path):
    """Runtime config only. REQUEST wins; --config is the local-run fallback."""
    raw = {}
    env = os.environ.get("REQUEST", "").strip()
    if env:
        raw = json.loads(env)
    elif path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    cfg = {**DEFAULT_CFG, **(raw if isinstance(raw, dict) else {})}
    if not cfg["pages"]:
        cfg["pages"] = [f"https://{HOST}/search/?q={quote(t)}" for t in cfg["queries"][:2]]
    if not cfg["pages"]:
        raise SystemExit("::error::REQUEST has neither 'pages' nor 'queries'.")
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="")
    cfg = load_cfg(ap.parse_args().config)

    # The config is echoed back so a reader of the artifact knows what produced it.
    report = {"requested": 0, "ok": 0, "failed": [], "pages": [], "cfg": cfg}
    products, details = {}, []

    def save(name, obj):
        with open(f"{OUT}/{name}", "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    def walk_products(node, sink):
        """Pull every product-shaped dict out of the JSON, at any depth.

        The API nests products differently per endpoint, and a key that is a dict
        on one page is a list on another - so shape, not path, is the selector.
        """
        if isinstance(node, dict):
            if isinstance(node.get("id"), int) and isinstance(node.get("title_fa"), str):
                sink[node["id"]] = node
            for v in node.values():
                walk_products(v, sink)
        elif isinstance(node, list):
            for v in node:
                walk_products(v, sink)

    def price_of(it):
        return ((it.get("default_variant") or {}).get("price") or {}).get("selling_price") or 0

    def flush():
        save("render-listing.json", {"count": len(products),
                                     "products": list(products.values())})
        save("render-details.json", details)
        save("render-report.json", report)

    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(args=["--no-sandbox"])
            ctx = br.new_context(user_agent=UA, locale="fa-IR",
                                 viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            sniffed = []

            def on_resp(r):
                # The page calls its own API while scrolling; harvesting those
                # responses means data survives even if a URL guess is wrong.
                if API_HOST in r.url and r.status == 200:
                    try:
                        walk_products(r.json(), products)
                        sniffed.append(r.url)
                    except Exception:
                        pass

            page.on("response", on_resp)

            def api(url):
                report["requested"] += 1
                last = None
                for i in range(3):
                    try:
                        res = page.evaluate(JS_FETCH, url)
                        if res and res.get("status") == 200 and "json" in res:
                            report["ok"] += 1
                            return res["json"]
                        last = str(res)[:200]
                    except Exception as e:
                        last = repr(e)[:200]
                    time.sleep(2 * (i + 1))
                report["failed"].append({"url": url, "error": last})
                return None

            def visit(url, scrolls=6):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                except Exception as e:
                    report["pages"].append({"url": url, "error": repr(e)[:150]})
                    return ""
                page.wait_for_timeout(4000)
                for _ in range(scrolls):
                    page.mouse.wheel(0, 2200)
                    page.wait_for_timeout(2000)
                html = page.content()
                txt = re.sub(r"<[^>]+>", " ",
                             re.sub(r"<script.*?</script>", "", html, flags=re.S))
                txt = re.sub(r"\s+", " ", txt)
                # A missing category path returns a 404 page under HTTP 200, so the
                # verdict has to come from the body, not the status code.
                report["pages"].append({"url": url, "html_len": len(html),
                                        "not_found": "پیدا نشد" in txt,
                                        "products_so_far": len(products)})
                return html

            visit(f"https://{HOST}/", scrolls=1)

            first = True
            for u in cfg["pages"]:
                html = visit(u)
                if html and first:
                    with open(f"{OUT}/render-search-page.capture.txt", "w",
                              encoding="utf-8") as f:
                        f.write(html)
                    first = False

            save("render-sniffed.json", sniffed)

            for term in cfg["queries"]:
                for p in range(1, cfg["max_pages"] + 1):
                    d = api(f"https://{API_HOST}/v1/search/?q={quote(term)}&page={p}")
                    if not d:
                        break
                    before = len(products)
                    walk_products(d, products)
                    if len(products) == before:
                        break
                    time.sleep(1)

            flush()

            # A keyword filter alone also catches accessories whose title repeats
            # the product name (a descaler, a door handle). The price floor is what
            # separates the appliance from the parts sold beside it.
            def is_target(it):
                t = it.get("title_fa") or ""
                if it.get("status") != "marketable":
                    return False
                if cfg["must_have"] and not any(w in t for w in cfg["must_have"]):
                    return False
                if any(w in t for w in cfg["must_not"]):
                    return False
                return cfg["min_price"] <= price_of(it) <= cfg["max_price"]

            def rate_count(it):
                return ((it.get("rating") or {}).get("count")) or 0

            pool = sorted((p for p in products.values() if is_target(p)),
                          key=lambda x: -rate_count(x))
            targets = pool[:cfg["top_n"]]
            report["targets"] = len(targets)

            for it in targets:
                pid = it["id"]
                d = api(f"https://{API_HOST}/v2/product/{pid}/")
                cm = []
                for cp in (1, 2, 3):
                    c = api(f"https://{API_HOST}/v1/product/{pid}/comments/?page={cp}")
                    got = ((c or {}).get("data") or {}).get("comments") or []
                    if not isinstance(got, list):
                        got = []
                    cm.extend(got)
                    if len(got) < 10:
                        break
                    time.sleep(1)
                details.append({"id": pid, "title": it.get("title_fa"),
                                "detail": d, "comments": cm})
                time.sleep(1)

            flush()
            br.close()
    finally:
        # Partial output beats no output: a crash mid-run still leaves what was found.
        flush()

    print(json.dumps({"products": len(products), "detailed": len(details),
                      "targets": report.get("targets"), "requested": report["requested"],
                      "ok": report["ok"], "pages": report["pages"]},
                     ensure_ascii=False)[:2500])


if __name__ == "__main__":
    main()
