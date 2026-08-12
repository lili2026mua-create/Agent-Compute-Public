#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""media_render.py — مالکِ یگانهٔ «چطور بستهٔ محتوایی نمایش داده می‌شود».

قاعدهٔ قطعیِ کاربر (۱۴۰۵-۰۵-۱۵) که این فایل اجرایی‌اش می‌کند:

  ۱. ویدیو از راهِ یک فایلِ HTML نمایش داده می‌شود تا در محیط قابلِ تماشا باشد.
     ⛔ بازنگریِ ۱۴۰۵-۰۵-۲۱ (تصمیمِ کاربر): ویدیو دیگر **داخلِ** صفحه جا داده نمی‌شود؛ صفحه به
     فایلِ همان ویدیو روی Google Drive **لینک** می‌دهد. چرا: امبدِ base64 با بندِ ۵ (سقفِ حجم)
     تضادِ ریاضی داشت — با کفِ کیفیتِ MIN_VIDEO_BPS هر ویدیوی بلندتر از حدودِ ۱۴ ثانیه از سقف
     می‌گذشت (بستهٔ b4593، ۴۱ ثانیه ← ۸۴۸ کیلوبایت). ویدیو از قبل روی Drive هست و خودِ Drive
     پخشش می‌کند، پس امبدِ سنگین کارِ اضافه بود. امبد فقط فالبکِ حالتی است که هنوز لینکی نیست.
  ۲. آن فایل **هیچ چیزی جز خودِ ویدیو(ها)** ندارد — نه رونوشت، نه جدولِ زیرنویس، نه گالریِ
     فریم، نه متنِ توضیحی. تک‌منظوره است.
  ۳. سلامتش **چک می‌شود**: نه شکسته باشد و نه توخالی؛ واقعاً باید داخلِ همان HTML پخش شود.
  ۴. اگر بسته مجموعهٔ تصاویر بود (یا تصویر هم داشت)، تصاویر مستقیم در گزارش پیش‌نمایش
     می‌شوند؛ فریم‌های استخراجیِ خودِ دستیار (frames/) **نمایش داده نمی‌شوند**.
  ۵. (۱۴۰۵-۰۵-۱۷) صفحه باید **زیرِ سقفِ حجم** بماند، وگرنه در عمل باز نمی‌شود و کاربر
     پیش‌نمایشی نمی‌بیند. سازنده‌اش **گامِ ۲** است، نه گامِ ۳.

چرا اسکریپت و نه فقط متنِ قاعده: تا امروز index.html همه‌چیز را با هم داشت (ویدیو + عکس +
جدولِ زیرنویس + رونوشت) و هیچ گاردی سلامتِ پخش را نمی‌سنجید — یعنی فایلِ خرابِ بی‌ویدیو هم
سبز رد می‌شد. قاعده‌ای که گارد ندارد برمی‌گردد.

کاربرد:
  python3 Sys.scripts/media_render.py --build <پوشهٔ بسته>            # ساختِ index.htmlِ تک‌منظوره
  python3 Sys.scripts/media_render.py --check <پوشهٔ بسته>            # فقط سنجشِ سلامت
خروج: 0 سالم · 1 شکسته/ناقص · 2 ورودیِ نادرست.
"""
import argparse
import base64
import glob
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile

VIDEO_EXT = (".mp4", ".mov", ".m4v")
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")

# کفِ حجمِ یک data-URIِ ویدیو/عکسِ سالم. زیرِ این عدد یعنی امبد نصفه‌کاره مانده
# (ffmpeg شکست خورده یا فایل صفربایت بوده) و صفحه عملاً چیزی برای پخش ندارد.
MIN_VIDEO_B64 = 40_000
MIN_PHOTO_B64 = 2_000

# ⛔ سقفِ حجمِ صفحهٔ پیش‌نمایش (تصمیمِ کاربر ۱۴۰۵-۰۵-۱۷ — قفل: preview-fits-inline-budget).
# چرا: پیش‌نمایشی که باز نشود اصلاً پیش‌نمایش نیست. اندازه‌گیریِ واقعیِ استخر پیش از این اصلاح:
# میانهٔ index.html برابرِ ۱٬۶۶۴ کیلوبایت بود و ۵۲ بسته از ۶۹ بالای ۱ مگابایت — یعنی سقفِ
# «حدودِ ۳۰۰ کیلوبایت»ِ سندِ گزارش عملاً در ۹۶٪ موارد نقض می‌شد و کسی نمی‌فهمید، چون هیچ گاردی
# حجم را نمی‌سنجید. علتش پریستِ سخاوتمندِ ۴۸۰px/crf30 بود.
PREVIEW_CAP = 300 * 1024          # سقفِ index.html
MIN_VIDEO_BPS = 90_000            # کفِ کیفیت؛ پایین‌تر از این ویدیو غیرقابلِ تماشا می‌شود
MAX_VIDEO_BPS = 500_000
AUDIO_BPS = 32_000

# ⛔ نشانه‌های «چیزی جز مدیا». اگر هرکدام در صفحه بود، صفحه تک‌منظوره نیست.
FORBIDDEN_TAGS = ("<table", "<pre", "<h1", "<h2", "<h3", "<p ", "<p>", "<figcaption")


def _media(bundle_dir):
    vids = sorted(sum([glob.glob(os.path.join(bundle_dir, "*" + e)) for e in VIDEO_EXT], []))
    phs = [p for p in sorted(sum([glob.glob(os.path.join(bundle_dir, "*" + e)) for e in PHOTO_EXT], []))
           if "/frames/" not in p.replace("\\", "/")
           and "/subtitle-strips/" not in p.replace("\\", "/")]
    return vids, phs


def _data_url(path, mime=None):
    mime = mime or mimetypes.guess_type(path)[0] or "application/octet-stream"
    return "data:%s;base64,%s" % (mime, base64.b64encode(open(path, "rb").read()).decode())


def _duration(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", src], capture_output=True, text=True)
    try:
        return max(float(r.stdout.strip()), 0.1)
    except ValueError:
        return 0.0


def _proxy(src, dst, budget_bytes=None):
    """پروکسیِ تطبیقی: نرخِ بیت از **بودجهٔ حجم و طولِ ویدیو** حساب می‌شود، نه پریستِ ثابت.

    پریستِ ثابتِ قبلی (۴۸۰px/crf30) برای کلیپِ ۱۰ ثانیه‌ای ~۵۲۰ کیلوبایت `HTML` می‌ساخت و برای
    کلیپِ یک‌دقیقه‌ای چند مگابایت — یعنی سقف عملاً بی‌معنا بود. حالا برعکس: اول سقف، بعد کیفیت.
    """
    budget = budget_bytes or int(PREVIEW_CAP * 0.70)   # ~۳۳٪ سربارِ base64 + سربارِ کانتینر
    dur = _duration(src)
    if dur <= 0:
        return False
    # ⛔ نرخِ صدا از بودجه کم می‌شود، وگرنه روی نرخِ ویدیو سوار می‌شود و سقف می‌شکند
    # (آزمونِ واقعی: بی این تفریق، کلیپِ ۹٫۷ ثانیه‌ای ۳۱۹ کیلوبایت درآمد؛ با آن ۲۸۵).
    bps = int(budget * 8 / dur) - AUDIO_BPS
    bps = max(MIN_VIDEO_BPS, min(MAX_VIDEO_BPS, bps))
    # ارتفاعِ هدف با نرخِ بیت بالا/پایین می‌رود تا تصویر گِل‌آلود نشود
    height = 640 if bps >= 400_000 else 480 if bps >= 220_000 else 360
    fps = 20 if bps >= 300_000 else 15
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", src,
                        "-vf", "scale=-2:%d,fps=%d" % (height, fps),
                        "-c:v", "libx264", "-b:v", str(bps), "-maxrate", str(int(bps * 1.4)),
                        "-bufsize", str(bps * 2), "-preset", "veryfast",
                        "-c:a", "aac", "-b:a", "%dk" % (AUDIO_BPS // 1000), "-ac", "1",
                        "-movflags", "+faststart", "-y", dst], capture_output=True)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0


def _drive_links(bundle_dir):
    """نگاشتِ rel_path → لینکِ Drive از status.json (خروجیِ pool_media_backend.publish).

    شکلِ قدیمیِ لیستِ خام (migrate_phase1) هم خوانده می‌شود، مثلِ pool_workspace.py."""
    sp = os.path.join(bundle_dir, "status.json")
    if not os.path.exists(sp):
        return {}
    try:
        rec = json.load(open(sp, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    df = rec.get("drive_files") or {}
    ups = df.get("uploaded", []) if isinstance(df, dict) else (df if isinstance(df, list) else [])
    return {u.get("rel_path"): u.get("web_view_link")
            for u in ups if u.get("rel_path") and u.get("web_view_link")}


def build(bundle_dir):
    """صفحهٔ تک‌منظورهٔ مدیا. ویدیو مقدم است؛ اگر ویدیو نبود، تصاویرِ خودِ بسته."""
    vids, phs = _media(bundle_dir)
    links = _drive_links(bundle_dir)
    body = ""
    if vids:
        with tempfile.TemporaryDirectory() as td:
            for i, v in enumerate(vids):
                url = links.get(os.path.basename(v))
                if url:
                    # حالتِ لینک — صفحه چند کیلوبایت می‌ماند و سقف همیشه رعایت می‌شود.
                    body += ('<a class="m" href="%s" target="_blank" rel="noopener">'
                             'تماشای ویدیو</a>' % url)
                    continue
                p = os.path.join(td, "p%d.mp4" % i)
                emb = _data_url(p, "video/mp4") if _proxy(v, p) else _data_url(v, "video/mp4")
                body += '<video controls playsinline preload="metadata" src="%s"></video>' % emb
    elif phs:
        for p in phs:
            body += '<img loading="lazy" src="%s" alt="">' % _data_url(p)

    doc = ('<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<style>html,body{margin:0;padding:0;background:#000}'
           'video,img{width:100%;max-height:100vh;object-fit:contain;display:block;background:#000}'
           'a.m{display:block;padding:24px;color:#fff;font:16px/1.6 sans-serif;text-align:center;text-decoration:none}'
           '</style></head><body>' + body + '</body></html>')
    out = os.path.join(bundle_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    return out, len(vids), len(phs)


def check(bundle_dir):
    """سنجشِ سلامتِ واقعی: شکسته نباشد، تک‌منظوره باشد، و واقعاً چیزی برای پخش داشته باشد."""
    path = os.path.join(bundle_dir, "index.html")
    vids, phs = _media(bundle_dir)
    fails = []
    if not os.path.exists(path):
        print("✗ index.html نیست")
        return 1
    s = open(path, encoding="utf-8", errors="replace").read()

    # ۱) ساختارِ نشکسته
    for t in ("<html", "</html>", "<body", "</body>"):
        if t not in s:
            fails.append("ساختارِ HTML شکسته است (%s نیست)" % t)

    # ۲) تک‌منظوره بودن
    for t in FORBIDDEN_TAGS:
        if t in s.lower():
            fails.append("صفحه تک‌منظوره نیست — «%s» دارد (فقط مدیا مجاز است)" % t)
            break

    # ۳) واقعاً قابلِ پخش/نمایش
    embeds = re.findall(r'src="data:(video|image)/[^;]+;base64,([^"]+)"', s)
    n_v = sum(1 for k, _ in embeds if k == "video")
    n_i = sum(1 for k, _ in embeds if k == "image")
    # ⛔ ۱۴۰۵-۰۵-۲۱: ویدیو حالا لینکِ Drive است، نه امبد. پس هر ویدیو باید یا امبد باشد یا لینک؛
    # چیزی که هیچ‌کدام نیست یعنی صفحه واقعاً چیزی برای تماشا ندارد (همان شکستی که این چک برایش هست).
    n_l = len(re.findall(r'<a class="m"', s))
    if vids:
        if n_v + n_l != len(vids):
            fails.append("%d ویدیو در بسته هست ولی %d امبد و %d لینک در صفحه"
                         % (len(vids), n_v, n_l))
        for k, b in embeds:
            if k == "video" and len(b) < MIN_VIDEO_B64:
                fails.append("یک ویدیوی امبدشده توخالی/نصفه است (%d بایتِ base64)" % len(b))
    elif phs:
        if n_i != len(phs):
            fails.append("%d تصویر در بسته هست ولی %d تای امبدشده در صفحه" % (len(phs), n_i))
        for k, b in embeds:
            if k == "image" and len(b) < MIN_PHOTO_B64:
                fails.append("یک تصویرِ امبدشده توخالی/نصفه است (%d بایتِ base64)" % len(b))
    else:
        fails.append("بسته هیچ مدیای قابلِ نمایشی ندارد")

    # ۴) ⛔ سقفِ حجم — پیش‌نمایشی که باز نشود پیش‌نمایش نیست (قفل: preview-fits-inline-budget)
    size = os.path.getsize(path)
    if size > PREVIEW_CAP:
        fails.append("صفحهٔ پیش‌نمایش %d کیلوبایت است و از سقفِ %d کیلوبایت گذشته — "
                     "با --build دوباره بساز تا انکودِ تطبیقی سبکش کند"
                     % (size // 1024, PREVIEW_CAP // 1024))

    if fails:
        for f in fails:
            print("✗ " + f)
        print("⛔ صفحهٔ نمایشِ بسته سالم نیست؛ گزارش حق ندارد آن را تحویل بدهد.", file=sys.stderr)
        return 1
    kind = "ویدیو" if vids else "تصویر"
    mode = "لینکِ Drive" if n_l and not n_v else "امبد"
    print("✅ صفحهٔ نمایش سالم و تک‌منظوره است (%s: %d آیتم، حالت=%s، %d کیلوبایت)."
          % (kind, n_v + n_l or n_i, mode, os.path.getsize(path) // 1024))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build")
    ap.add_argument("--check")
    a = ap.parse_args()
    if a.build:
        if not os.path.isdir(a.build):
            print("✗ پوشهٔ بسته یافت نشد", file=sys.stderr)
            return 2
        out, nv, np_ = build(a.build)
        print("✓ ساخته شد: %s (%d ویدیو، %d تصویر)" % (out, nv, np_))
        return check(a.build)
    if a.check:
        if not os.path.isdir(a.check):
            print("✗ پوشهٔ بسته یافت نشد", file=sys.stderr)
            return 2
        return check(a.check)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
