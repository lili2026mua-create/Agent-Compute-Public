#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pool_enrich.py — غنی‌سازیِ بسته‌های استخرِ دریافت + بستهٔ HTML + برچسبِ «تحلیل‌شده».

اجرا روی GitHub Actions، روی یک `pool/` محلی که `pool_workspace.py materialize` از
`Memory-Workflow/intake-pool/state/` روی `main` می‌سازد (⛔ فازِ ۲ — نه چک‌آوتِ برنچِ intake-pool،
منسوخ). برای هر بستهٔ کشیده‌شده (زیرِ pool/telegram/<key>/ و هر ویدیوی pool/drive/) گام‌های ۱–۲
و ۷ ایجنتِ video-interpreter را می‌سازد:

  - رونویسیِ صوت (faster-whisper) + فریم‌ها (خوانشِ ماشینیِ زیرنویس حذف شد — ۱۴۰۵-۰۵-۲۱)
    → بازاستفادهٔ کاملِ منطقِ Sys.scripts/tg_enrich.py (transcript.md + enrichment.json + frames/).
  - صفحهٔ نمایشِ **تک‌منظورهٔ** بسته (index.html) از راهِ media_render.py: فقط ویدیو(ها)،
    یا اگر ویدیو نبود فقط تصاویرِ خودِ بسته — با چکِ سلامتِ اجباری.
  - REVIEW.md با برچسبِ «تحلیل‌شده» + status.json (وضعیت برای چرخهٔ بازبینیِ intake-pool).

  - آیتم‌های ماشینیِ قرارداد (گامِ ۲.۳ی سهمِ CI): transcript.json + data-inventory.json.
  - گامِ ۲.۴ی واقعی: interpretation_contract.py --check --stage final. برچسبِ «تحلیل‌شده» فقط وقتی
    زده می‌شود که قرارداد کامل باشد؛ وگرنه بسته «کشیده‌شده» می‌ماند و کمبودش صریح ثبت می‌شود.

idempotent: بسته‌ای که index.html دارد رد می‌شود (مگر --force). گام‌های ۳–۶ (تلفیق/چکِ نکات/خلأ/جنس)
استدلالِ زندهٔ Claude در سشنِ بازبینی‌اند و این‌جا انجام نمی‌شوند.

⛔ درسِ ۱۴۰۵-۰۵-۱۵ (چرا گاردِ ۲.۴ این‌جاست): نسخهٔ قبلی بعد از گام‌های ۱/۲/۷ بی‌قید «تحلیل‌شده»
می‌زد، درحالی‌که گامِ ۲.۳ (آیتم‌های قرارداد) هرگز اجرا نشده بود. نتیجه: برچسب دروغ می‌گفت، صفِ
گامِ ۳.۱ با بسته‌های ناتمام پر می‌شد و هر ۶۹ بستهٔ استخر «پیش‌نمایشِ ناقص» چاپ می‌شدند بی‌آنکه
هیچ گاردی ببیندش. برچسب حالا از خروجیِ واقعی مشتق می‌شود، نه از اجرای صرفِ غنی‌سازی.
"""
import argparse
import base64
import glob
import html as _html
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_enrich  # noqa: E402  (بازاستفاده از منطقِ رونویسی/فریم/زیرنویس)
import media_render  # noqa: E402  (مالکِ یگانهٔ صفحهٔ نمایشِ تک‌منظورهٔ بسته)

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".m4v")
MEDIA_EXT = VIDEO_EXT + PHOTO_EXT


def sniff_media_ext(path):
    """پسوندِ مدیا را از امضای بایت‌های آغازین حدس بزن (خودترمیمیِ فایلِ بی‌پسوند).

    فایلی که درایو بی‌پسوند نام‌گذاری شده (مثلِ ویدیوی «اسکیل فایندر») بی این کار در
    گلابِ VIDEO_EXT جا می‌ماند و بی‌صدا از استخر حذف می‌شود. با سنجشِ magic bytes پسوندش
    را بازمی‌گردانیم تا غنی‌سازی شود، نه اینکه ساکت رد شود.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if head[4:8] == b"ftyp":
        return ".mp4"                        # ISO-BMFF (mp4/mov/m4v)
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def heal_extensionless_media(drive_dir):
    """هر فایلِ بی‌پسوندِ مدیا در pool/drive/ را با پسوندِ درست بازنام‌گذاری کن."""
    healed = []
    for p in sorted(glob.glob(os.path.join(drive_dir, "*"))):
        if not os.path.isfile(p):
            continue
        if p.lower().endswith(MEDIA_EXT):
            continue
        ext = sniff_media_ext(p)
        if not ext:
            continue
        np = p + ext
        if not os.path.exists(np):
            os.replace(p, np)
            healed.append((os.path.basename(p), os.path.basename(np)))
    for old, new in healed:
        print(f"[pool_enrich] خودترمیمی: «{old}» → «{new}» (پسوندِ مدیا از امضای بایت).")
    return healed


def data_url(path, mime=None):
    mt = mime or mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return f"data:{mt};base64," + base64.b64encode(f.read()).decode()


def compress_video(src, dst):
    """پروکسیِ سبک برای امبدِ درون‌خط (عرض ~۴۸۰، crf ۳۰) — طبقِ قاعدهٔ گامِ ۷."""
    rc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", src,
         "-vf", "scale=480:-2", "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
         "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", "-y", dst],
        capture_output=True)
    return rc.returncode == 0 and os.path.exists(dst)


def build_html(key, bundle_dir, summary, source):
    """گامِ ۷ — صفحهٔ نمایشِ بسته. ⛔ مالکِ یگانهٔ قالبِ نمایش `media_render.py` است.

    قاعدهٔ قطعیِ کاربر (۱۴۰۵-۰۵-۱۵): این صفحه **تک‌منظوره** است — فقط ویدیو(ها)، و اگر ویدیو
    نبود فقط تصاویرِ خودِ بسته. نه رونوشت، نه جدولِ زیرنویس، نه گالریِ فریم. نسخهٔ قبلی همه را
    با هم داشت و هیچ گاردی سلامتِ پخش را نمی‌سنجید. سلامتش این‌جا **چک** می‌شود و نتیجه‌اش در
    status.json می‌نشیند تا گزارش هرگز صفحهٔ شکسته تحویل ندهد. قفل: media-display-is-single-purpose-html
    """
    media_render.build(bundle_dir)
    ok = media_render.check(bundle_dir) == 0
    vids, phs = media_render._media(bundle_dir)
    return {"media_ok": ok, "media_kind": "video" if vids else ("photo" if phs else "none"),
            "media_count": len(vids) or len(phs)}


CONTRACT_CHECKER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "interpretation_contract.py")

# آیتم‌هایی که CI داده‌شان را دارد و می‌تواند بی‌قضاوت بسازد (سهمِ ماشین از گامِ ۲.۳).
# بقیه (onscreen_text/fusion/verified_points/content_gaps/genre_classification/external_sources)
# استدلالِ زندهٔ Claude‌اند و این‌جا **جعل نمی‌شوند** — در سیاهه «غایب» علامت می‌خورند.
AGENT_ONLY_ITEMS = [
    ("onscreen_text", "onscreen-text.json"), ("fusion", "fusion.json"),
    ("verified_points", "verified-points.json"), ("content_gaps", "content-gaps.json"),
    ("genre_classification", "genre-classification.json"),
    ("external_sources", "external-sources.json"),
]


def _probe_duration(bundle_dir, fname):
    """طولِ ویدیو بر حسبِ ثانیه با ffprobe؛ None اگر در دسترس نبود."""
    cands = [os.path.join(bundle_dir, fname)] if fname else []
    cands += sorted(sum([glob.glob(os.path.join(bundle_dir, "*" + e)) for e in VIDEO_EXT], []))
    for c in cands:
        if not os.path.exists(c):
            continue
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", c],
                           capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except Exception:  # noqa: BLE001
            continue
    return None


def _jdump(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_machine_contract_items(bundle_dir, key):
    """گامِ ۲.۳ — سهمِ ماشین: transcript.json + data-inventory.json.

    ⛔ status در سیاهه از روی **کیفیتِ محتوا** تعیین می‌شود، نه وجودِ فایل: رونوشتی که هست ولی
    برای طولِ ویدیو بی‌معنا کوتاه است «مخدوش» است، نه «ساخته‌شده». همین سیاهه چیزی است که
    mapping-specialist در بخشِ ۰ چاپ می‌کند تا خواننده بداند به کدام بخش چقدر اعتماد کند.
    """
    epath = os.path.join(bundle_dir, "enrichment.json")
    enr = json.load(open(epath, encoding="utf-8")) if os.path.exists(epath) else {}
    vids = enr.get("videos", []) or []
    v0 = vids[0] if vids else {}

    # --- transcript.json (گامِ ۱) ---
    tpath = os.path.join(bundle_dir, "transcript.md")
    body = ""
    if os.path.exists(tpath):
        raw = open(tpath, encoding="utf-8").read()
        body = raw.split("**رونوشتِ صوت**", 1)[-1].split(":\n", 1)[-1].strip()
    chars = int(v0.get("transcript_chars") or len(body))
    dur = _probe_duration(bundle_dir, v0.get("file"))
    # آستانهٔ پوچی: کمتر از ۳ کاراکتر بر ثانیه گفتار عملاً رونوشت نیست. اگر طول ناشناخته
    # ماند (ffprobe نبود)، کفِ مطلقِ ۵۰ کاراکتر ملاک است — سکوتِ گارد بدترین حالت است.
    speech = v0.get("audio_verdict") == "speech"
    thin = bool(speech and (chars < dur * 3 if dur else chars < 50))
    # ⛔ درسِ ۱۴۰۵-۰۵-۱۸: «کوتاه‌بودن» تنها نشانهٔ خرابی نیست. رونوشتی که با زبانِ اشتباه
    # ساخته شده ممکن است پُر و روان به‌نظر برسد و کاملاً زباله باشد. سه نشانهٔ مستقل:
    #   الف) کم‌حجم بودن نسبت به طولِ گفتار (thin)
    #   ب) زبان از روی env تحمیل شده، نه تشخیص داده شده (lang_source = env-fallback)
    #   ج) اطمینانِ تشخیصِ زبان پایین است
    #   د) پوششِ زمانی کم است — بخشی از صوت اصلاً شنیده نشده
    lang_src = v0.get("lang_source")
    lang_prob = v0.get("lang_probability")
    cov = v0.get("transcript_coverage")
    lang_forced = lang_src == "env-fallback"
    lang_unsure = (lang_prob is not None and lang_prob < 0.5) or lang_src == "detected-low-confidence"
    cov_bad = cov is not None and cov < 0.60
    corrupt = bool(thin or lang_forced or lang_unsure or cov_bad)
    reasons = []
    if thin:
        reasons.append("%d کاراکتر برای %s ثانیه گفتار" % (chars, ("%.2f" % dur) if dur else "؟"))
    if lang_forced:
        reasons.append("زبان از env تحمیل شد (تشخیص مطمئن نبود) — احتمالِ رونویسی به زبانِ اشتباه")
    if lang_unsure:
        reasons.append("اطمینانِ تشخیصِ زبان %s" % lang_prob)
    if cov_bad:
        reasons.append("پوششِ زمانیِ %s — بخشی از صوت به متن نیامده" % cov)
    note = ("⛔ مخدوش — " + "؛ ".join(reasons) +
            ". این رونوشت نباید در گزارش استفاده شود و ⛔ هیچ نامِ محصول/ابزاری از آن استنتاج نمی‌شود."
            ) if corrupt else "رونویسیِ خودکار؛ پیش از نقلِ قول با خوانشِ بصری تطبیق داده شود."
    _jdump(os.path.join(bundle_dir, "transcript.json"), [{
        "start": 0.0, "end": dur, "text": body or None,
        "model": enr.get("whisper_model", "whisper"),
        "lang_detected": v0.get("lang"),
        "lang_probability": lang_prob,
        "lang_source": lang_src,
        "coverage": cov,
        "spoken_seconds": v0.get("spoken_seconds"),
        "segments_file": "transcript-segments.json",
        "quality": "مخدوش" if corrupt else "سالم",
        "confidence_note": note,
    }])

    vid = v0.get("id") or key
    nframes = len(glob.glob(os.path.join(bundle_dir, "frames", "*")))

    items = [
        {"item": "file_id", "file": "enrichment.json", "status": "ساخته‌شده",
         "source": "نمونه‌برداریِ ابزاری",
         "coverage": "%d ویدیو، %d فریم" % (len(vids), nframes)},
        {"item": "transcript", "file": "transcript.json",
         "status": "مخدوش" if corrupt else "ساخته‌شده", "source": "رونویسیِ صوت",
         "coverage": "%d کاراکتر برای %.2f ثانیه · زبان=%s (%s، اطمینان %s) · پوششِ زمانی=%s"
                     % (chars, dur or 0, v0.get("lang"), lang_src, lang_prob, cov)},
    ]
    for k, f in AGENT_ONLY_ITEMS:
        items.append({"item": k, "file": f, "status": "غایب", "source": None,
                      "coverage": "گامِ استدلالیِ video-interpreter هنوز اجرا نشده"})
    items += [
        {"item": "media", "file": "frames/ + index.html (+ mp4)", "status": "ساخته‌شده",
         "source": "نمونه‌برداریِ ابزاری",
         "coverage": "%d فریم + index.html" % nframes},
        {"item": "builder_agents_and_note", "file": "(بدون فایل — نمایش‌گر می‌نویسد)",
         "status": "غایب", "source": None, "coverage": "در زمانِ رندر نوشته می‌شود"},
        {"item": "summary_proposals", "file": "genre-classification.json", "status": "غایب",
         "source": None, "coverage": "وابسته به گامِ ۶"},
        {"item": "data_inventory", "file": "data-inventory.json", "status": "ساخته‌شده",
         "source": "نمونه‌برداریِ ابزاری", "coverage": "%d رکورد" % (len(items) + 4)},
    ]
    _jdump(os.path.join(bundle_dir, "data-inventory.json"), {
        "video_id": vid,
        "generated_by": "pool_enrich.py (سهمِ ماشین از گامِ ۲.۳؛ آیتم‌های استدلالی هنوز غایب‌اند)",
        "items": items,
    })


def contract_gate(bundle_dir):
    """گامِ ۲.۴ — چکِ واقعیِ کاملیِ قرارداد. خروجی: (ok, missing_keys)."""
    if not os.path.exists(CONTRACT_CHECKER):
        return False, ["(چکرِ قرارداد یافت نشد)"]
    r = subprocess.run([sys.executable, CONTRACT_CHECKER, "--check", bundle_dir,
                        "--stage", "final"], capture_output=True, text=True)
    if r.returncode == 0:
        return True, []
    missing = [ln.split("—")[0].replace("✗", "").strip()
               for ln in r.stdout.splitlines() if ln.strip().startswith("✗")]
    return False, missing


REVIEW_TEMPLATE = """# فایلِ بررسیِ موقتِ بستهٔ {key}

**برچسب:** {status}
**منبع:** {source}
**غنی‌سازی:** {ts}
**کاملیِ قرارداد (گامِ ۲.۴):** {gate}

> این فایلِ **موقت** است و روی برنچِ `intake-pool` می‌ماند تا سشنِ بازبینی آن را کامل کند.
> CI گام‌های ۱–۲ (رونویسی/زیرنویس/فریم)، سهمِ ماشین از گامِ ۲.۳ (`transcript.json` +
> `data-inventory.json`) و گامِ ۷ (`index.html`) را انجام داده است.
> ⛔ گام‌های ۳–۶ استدلالِ زندهٔ Claude‌اند و CI آن‌ها را **جعل نمی‌کند**.
> چرخهٔ وضعیت: `کشیده‌شده → تحلیل‌شده → چاپ‌شده → بسته‌شده`.

## کمبودِ قرارداد (خروجیِ واقعیِ گامِ ۲.۴)
{missing_block}

## راهِ بستنِ کمبود
سشنِ بازبینی در نقشِ `video-interpreter` گام‌های ۳–۶ را می‌سازد، بعد:

```
python3 Sys.scripts/interpretation_contract.py --check <پوشهٔ بسته> --stage final
```

کدِ ۰ که شد، وضعیت با `Memory-Workflow/review-claim/request.json` به `تحلیل‌شده` می‌رود
(گامِ ۲.۵) و تازه آن‌وقت بسته واردِ صفِ گامِ ۳ می‌شود.
"""


def enrich_bundle(bundle_dir, source, force=False):
    key = os.path.basename(bundle_dir.rstrip("/"))
    sp = os.path.join(bundle_dir, "status.json")
    prev = {}
    if os.path.exists(sp):
        try:
            prev = json.load(open(sp, encoding="utf-8"))
        except Exception:
            prev = {}
    # ⛔ فازِ ۲ (۱۴۰۵-۰۵-۲۱): پیش از بازطراحیِ Drive، ایدمپوتنسی از رویِ وجودِ محلیِ index.html
    # سنجیده می‌شد. حالا index.html به Drive منتشر می‌شود و معمولاً محلی نیست (pool_workspace
    # فقط رسانهٔ لازم را stage می‌کند، نه همه‌چیز را) — چکِ فایل همیشه False می‌داد و هر اجرا کلِ
    # استخر را دوباره whisper می‌کرد. منبعِ حقیقتِ ایدمپوتنسی حالا status.json است (که همیشه
    # materialize می‌شود، چون فایلِ کوچک است): هر بسته‌ای که به «تحلیل‌شده» یا جلوترش رسیده باشد
    # قبلاً کامل غنی شده.
    if not force and prev.get("status") in ("تحلیل‌شده", "چاپ‌شده", "بسته‌شده"):
        print(f"  ↷ {key}: قبلاً غنی‌سازی شده (status={prev.get('status')}) — رد شد.")
        return None
    summary = tg_enrich.enrich_key(bundle_dir)          # گام ۱–۲ (+frames/transcript/enrichment)
    media = build_html(key, bundle_dir, summary, source)  # گام ۷ (تک‌منظوره + چکِ سلامت)
    try:
        write_machine_contract_items(bundle_dir, key)   # گام ۲.۳ — سهمِ ماشین
    except Exception as e:                              # noqa: BLE001
        print(f"  ⚠ {key}: ساختِ آیتم‌های ماشینیِ قرارداد نشد: {e}")
    ok, missing = contract_gate(bundle_dir)             # گام ۲.۴ — چکِ واقعی
    ts = datetime.now(timezone.utc).isoformat()
    gate_txt = "✅ کامل (stage=final)" if ok else f"⛔ ناقص — {len(missing)} آیتم"
    miss_block = ("همهٔ آیتم‌های قرارداد ساخته شده‌اند."
                  if ok else "\n".join(f"- [ ] `{m}` — هنوز ساخته نشده" for m in missing))
    # ⛔ روی غنی‌سازیِ دوباره (force)، چرخهٔ بازبینی را عقب نبر: بسته‌ای که «چاپ‌شده» یا «بسته‌شده»
    # است نباید بی‌صدا به «تحلیل‌شده» برگردد و دوباره به صف برود (درسِ ۱۴۰۵-۰۵-۰۷).
    #
    # ⛔ اصلاحِ ۱۴۰۵-۰۵-۱۳ — جرثقیل، نه چسب: نسخهٔ قبلی وضعیتِ قبلی را **بی‌قید** حفظ می‌کرد، پس
    # بسته‌ای که هنوز «کشیده‌شده» بود و تازه غنی‌سازی می‌شد، روی «کشیده‌شده» می‌ماند — یعنی گامِ ۲
    # واقعاً انجام شده بود ولی بسته هرگز وارد صفِ گامِ ۳ نمی‌شد و **نامرئی** رها می‌ماند. ده بستهٔ
    # عکسیِ تلگرام دقیقاً همین‌طور گیر کرده بودند و هیچ گاردی نمی‌دیدشان.
    # قاعدهٔ درست یک‌طرفه است: وضعیت فقط جلو می‌رود. هرچه **عقب‌تر** از «تحلیل‌شده» باشد به
    # «تحلیل‌شده» ارتقا می‌یابد؛ هرچه در همان‌جا یا **جلوتر** باشد دست‌نخورده می‌ماند.
    #
    # ⛔ اصلاحِ ۱۴۰۵-۰۵-۱۵ — برچسب از خروجیِ واقعی مشتق می‌شود، نه از اجرای صرفِ غنی‌سازی:
    # «تحلیل‌شده» یعنی گامِ ۲ **کامل** شده (۲.۱ تا ۲.۴)، نه فقط ۲.۱/۲.۲. تا وقتی گیتِ ۲.۴
    # سبز نشده بسته روی «کشیده‌شده» می‌ماند و واردِ صفِ گامِ ۳ نمی‌شود.
    RANK = {"لیست‌شده": 0, "کشیده‌شده": 1, "تحلیل‌شده": 2, "چاپ‌شده": 3, "بسته‌شده": 4}
    prev_status = prev.get("status", "")
    target = "تحلیل‌شده" if ok else "کشیده‌شده"
    if RANK.get(prev_status, -1) >= RANK[target]:
        status = prev_status                      # همان‌جا یا جلوتر — دست نزن (فقط جلو می‌رود)
    else:
        status = target                           # عقب‌تر (یا ناشناخته) — ارتقا بده
        if prev_status:
            print(f"  ↑ {key}: وضعیت از «{prev_status}» به «{target}» ارتقا یافت.")
    if not ok:
        print(f"  ⛔ {key}: گیتِ ۲.۴ رد شد — {len(missing)} آیتمِ قرارداد کم است: {', '.join(missing)}")
    rec = {"key": key, "source": source,
           "status": status,
           "contract_complete": ok,          # گامِ ۲.۴ — ماشین‌خوان، برای گاردِ گامِ ۳.۳
           "contract_missing": missing,
           "media_ok": media["media_ok"],    # سلامتِ صفحهٔ نمایش (قفل: media-display-is-single-purpose-html)
           "media_kind": media["media_kind"],
           "media_count": media["media_count"],
           "enriched_utc": prev.get("enriched_utc", ts)}
    if prev.get("owner"):
        rec["owner"] = prev["owner"]
    if prev:
        rec["re_enriched_utc"] = ts        # ردِ صریحِ اینکه محتوا دوباره غنی شد (نه شکستِ خاموش)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    with open(os.path.join(bundle_dir, "REVIEW.md"), "w", encoding="utf-8") as f:
        f.write(REVIEW_TEMPLATE.format(key=key, source=source, ts=ts, status=status,
                                       gate=gate_txt, missing_block=miss_block))
    print(f"  ✓ {key}: غنی‌سازی + نمایش ({media['media_kind']}×{media['media_count']}، "
          f"سالم={media['media_ok']}) · status={rec['status']} · قرارداد={'کامل' if ok else 'ناقص'}.")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="pool", help="ریشهٔ pool/ محلی — از pool_workspace.py materialize (نه چک‌آوتِ برنچ)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keys", default="",
                    help="کلیدهای کاما-جدا؛ اگر داده شود فقط همین بسته‌ها غنی می‌شوند. "
                         "چرا لازم است: --force بدونِ آن کلِ استخر را دوباره با whisper می‌سازد "
                         "(ساعت‌ها اجرا و هزینه) درحالی‌که معمولاً چند بستهٔ مشخص هدف‌اند.")
    a = ap.parse_args()
    only = {k.strip() for k in a.keys.split(",") if k.strip()}

    bundles = []
    for d in sorted(glob.glob(os.path.join(a.pool, "telegram", "*"))):
        if os.path.isdir(d):
            bundles.append((d, "telegram"))
    # خودترمیمی: فایلِ مدیای بی‌پسوند (مثلِ ویدیوی درایوِ بی‌پسوند) را پیش از گلاب پسوند بده.
    heal_extensionless_media(os.path.join(a.pool, "drive"))
    drive_bds = {}   # dedup: مسیرِ پوشهٔ بسته → True
    # (۱) ویدیوی لوسِ تازه‌کشیده در drive/ → به پوشهٔ خودش منتقل کن (بستهٔ نو).
    for v in sorted(sum([glob.glob(os.path.join(a.pool, "drive", "*" + e)) for e in VIDEO_EXT], [])):
        stem = os.path.splitext(os.path.basename(v))[0]
        bd = os.path.join(a.pool, "drive", stem)
        os.makedirs(bd, exist_ok=True)
        nv = os.path.join(bd, os.path.basename(v))
        if not os.path.exists(nv):
            os.replace(v, nv)
        drive_bds[bd] = True
    # (۲) بستهٔ درایوِ ازقبل‌پوشه‌شده (drive/<stem>/ که ویدیو دارد) → برای غنی‌سازیِ دوباره دیده شود.
    #     ⛔ درسِ ۱۴۰۵-۰۵-۰۷: بی این بند، پس از اولین غنی‌سازی ویدیو داخلِ پوشه می‌رفت و گلابِ لوسِ
    #     بند (۱) دیگر نمی‌دیدش؛ نتیجه: --force روی هر بستهٔ درایو بی‌اثر بود (Skil finder با small ماند).
    for d in sorted(glob.glob(os.path.join(a.pool, "drive", "*"))):
        if os.path.isdir(d) and any(glob.glob(os.path.join(d, "*" + e)) for e in VIDEO_EXT):
            drive_bds[d] = True
    for bd in sorted(drive_bds):
        bundles.append((bd, "drive"))

    if only:
        before = len(bundles)
        bundles = [(bd, src) for bd, src in bundles
                   if os.path.basename(bd.rstrip("/")) in only]
        got = {os.path.basename(bd.rstrip("/")) for bd, _ in bundles}
        missing = sorted(only - got)
        print(f"[pool_enrich] فیلترِ --keys: {len(bundles)} از {before} بسته انتخاب شد.")
        if missing:
            # شکستِ خاموش ممنوع: کلیدی که خواسته شده ولی پیدا نشد باید دیده شود.
            print(f"::error::این کلیدها در استخر پیدا نشدند: {', '.join(missing)}")
            sys.exit(1)

    if not bundles:
        # شکستِ خاموش ممنوع (workflow-builder بندِ ۳): «کاری نبود» رد صریح می‌گذارد، نه سکوت.
        print("[pool_enrich] بسته‌ای برای غنی‌سازی نبود (idle).")
        return
    print(f"[pool_enrich] {len(bundles)} بسته با whisper={tg_enrich.WHISPER_MODEL} …")
    done = 0
    failed = 0
    skipped = 0
    for bd, src in bundles:
        try:
            r = enrich_bundle(bd, src, a.force)
            if r:
                done += 1
            else:
                skipped += 1          # قبلاً غنی‌سازی‌شده (idempotent)
        except Exception as e:  # noqa
            failed += 1
            print(f"  ⚠ {os.path.basename(bd)}: خطا — {e}")
    print(f"[pool_enrich] تازه={done} · ردشده(قبلاً)={skipped} · شکست‌خورده={failed} از {len(bundles)}.")
    # ⛔ اگر همهٔ بسته‌های تازه شکست خوردند و هیچ‌چیز موفق/رد نشد، اجرا باید قرمز شود (نه سبزِ خاموش).
    if failed and done == 0 and skipped == 0:
        print(f"::error::pool_enrich: هر {failed} بسته در غنی‌سازی شکست خورد — خروجی‌ای تولید نشد.")
        sys.exit(1)
    # شکستِ جزئی هم باید دیده شود، ولی اجرا را قرمز نکند (بقیه موفق بوده‌اند).
    if failed:
        print(f"::warning::pool_enrich: {failed} بسته شکست خورد ولی {done+skipped} بسته سالم بود.")


if __name__ == "__main__":
    main()
