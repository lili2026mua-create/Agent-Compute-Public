#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_enrich.py — لایهٔ «غنی‌سازی»ِ روتینِ telegram-watch (اجرا در GitHub Actions).

چرا: کارِ سنگینِ استخراجِ فریم و رونویسیِ صوت (whisper) قبلاً داخلِ تسکِ تحلیلِ Cowork انجام
می‌شد که (۱) whisper در سندباکسِ Cowork بلاک بود و مدام ری‌تری می‌زد، و (۲) هر دستورِ شل یک
پاپ‌آپِ «Allow Sandbox Exec?» می‌داد. این اسکریپت آن کار را به Actions منتقل می‌کند: نه پاپ‌آپی
هست و نه whisper بلاک است. خروجی (فریم‌ها + رونوشت) کنارِ مدیا روی برنچِ tg-media منتشر می‌شود،
پس تسکِ تحلیل فقط رونوشت/فریمِ آماده را می‌خواند و دیگر چیزی دانلود/رونویسی نمی‌کند.

ورودی: یک پوشهٔ ریشه (مثلِ _tgwork/watch) که زیرش پوشهٔ <key>/ هر بستهٔ تازه است.
برای هر <key>/:
  - از هر ویدیوی .mp4 تا N فریمِ یکنواخت استخراج و در <key>/frames/ ذخیره می‌کند.
  - اگر ویدیو استریمِ صوتی داشت، با faster-whisper رونویسی و به <key>/transcript.md اضافه می‌کند.
  - عکس‌های .jpg خودشان فریم‌اند؛ در transcript.md فقط فهرست می‌شوند.
  - <key>/enrichment.json را با خلاصهٔ وضعیت می‌نویسد.
هر خطا per-file گرفته می‌شود تا یک فایلِ خراب کلِ بسته را از کار نیندازد.
"""

import os
import sys
import json
import glob
import subprocess
import argparse

# ⛔ سقفِ تعدادِ فریم برداشته شد (تصمیمِ کاربر ۱۴۰۵-۰۵-۲۱، پس از حذفِ خوانشِ ماشینی).
# چرا: فریم حالا **تنها** منبعِ متنِ روی‌صفحه است. با سقفِ ثابت، فاصلهٔ نمونه‌برداری روی ویدیوی
# بلند کش می‌آمد — ویدیوی دو دقیقه‌ای عملاً هر شش ثانیه یک فریم می‌گرفت و هرچه بینشان بود
# نادیده می‌رفت. کاربر گفت اهمیتِ محتوا این هزینه را توجیه می‌کند. پس فقط فاصله ثابت است:
# یک فریم در هر FRAME_STEP_SECONDS ثانیه، بی‌هیچ سقفی.
FRAME_STEP_SECONDS = 2.0
# ⛔ حذفِ خوانشِ ماشینیِ زیرنویس (تصمیمِ کاربر ۱۴۰۵-۰۵-۲۱ — قفل: `onscreen-text-is-visual-only`)
# چرا حذف شد و نه اصلاح: آنچه OCR می‌خواند زیرنویسِ سوختهٔ ویدیو بود، و آن زیرنویس چیزی جز
# خودِ گفتارِ گوینده نیست — یعنی تکرارِ ضعیفِ همان کاری که رونویسیِ صوت با اطمینانِ بالا انجام
# می‌دهد. اطلاعاتی که واقعاً فقط از تصویر می‌آید (نامِ ابزار، نشانی، دکمهٔ رابط، لوگو) دقیقاً
# همان‌هایی بودند که OCR نمی‌گرفت: موتور روی فارسی تنظیم بود و هر تکهٔ لاتین را زباله می‌خواند
# (بستهٔ b4593: «Google AI Studio» شد «ع6اوصصتی وااباط»)، و لوگوی محو اصلاً داده‌ای برای خواندن
# نداشت. سه دلیلِ بالا با تنظیم درست نمی‌شدند. بودجهٔ آزادشده صرفِ فریمِ بیشتر شد.
# منبعِ یگانهٔ متنِ روی‌صفحه از این پس: خوانشِ بصریِ frames/ توسطِ سشنِ زنده.

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")   # ورک‌فلو medium پاس می‌دهد؛ small فقط پیش‌فرضِ لوکال
# ⛔ درسِ ۱۴۰۵-۰۵-۱۸ (باگِ لهجه‌کور): این متغیر روی "fa" pin شده بود و ورک‌فلو هم "fa" پاس می‌داد،
# پس هر ویدیوی انگلیسی/عربی/… به‌زور فارسی رونویسی می‌شد و خروجی زبالهٔ محض بود
# (نمونهٔ واقعی: بستهٔ 1sYejG… — ویدیوی انگلیسیِ Claude Code که «سکلوڈ کود پلگنز…» شد).
# حالا پیش‌فرض auto است: زبان در پاسِ اول تشخیص داده می‌شود و در پاسِ دوم pin می‌شود.
# pin دستی فقط وقتی اعمال می‌شود که تشخیص مطمئن نباشد (زیرِ LANG_MIN_PROB).
WHISPER_LANG = os.environ.get("WHISPER_LANG", "auto")
LANG_MIN_PROB = float(os.environ.get("WHISPER_LANG_MIN_PROB", "0.50"))
# پوششِ زمانی: نسبتِ ثانیه‌های رونویسی‌شده به کلِ صوت. زیرِ این آستانه یعنی بخشی از صدا شنیده نشده.
MIN_TRANSCRIPT_COVERAGE = float(os.environ.get("MIN_TRANSCRIPT_COVERAGE", "0.60"))
# initial_prompt کوتاه، فقط برای تثبیتِ املا/زبان (نه تحمیلِ موضوع). قابلِ override با env.
# ⛔ initial_prompt زبان‌ویژه است: پرامپتِ فارسی روی صوتِ انگلیسی خودش عاملِ انحراف می‌شود.
# پس فقط وقتی تزریق می‌شود که زبانِ تشخیص‌داده‌شده همان زبانِ پرامپت باشد.
WHISPER_PROMPTS = {
    "fa": "این یک ویدیوی آموزشیِ فارسی است.",
    "en": "This is an English tutorial video about software and AI tools.",
    "ar": "هذا فيديو تعليمي باللغة العربية.",
}
WHISPER_PROMPT = os.environ.get("WHISPER_PROMPT", "")   # override دستی؛ خالی = انتخابِ خودکار


def run(cmd, timeout=1800):
    """اجرای دستور و برگرداندنِ (rc, stdout, stderr)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def ffprobe_duration(path):
    rc, out, _ = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", path], timeout=60)
    try:
        return float(out.strip())
    except Exception:
        return 0.0


def video_size(path):
    """(w, h) ویدیو؛ (0, 0) اگر نشد."""
    rc, out, _ = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                      "-show_entries", "stream=width,height", "-of", "csv=p=0", path], timeout=60)
    try:
        w, h = out.strip().split(",")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def has_audio_stream(path):
    rc, out, _ = run(["ffprobe", "-v", "error", "-select_streams", "a",
                      "-show_entries", "stream=codec_type", "-of", "csv=p=0", path], timeout=60)
    return "audio" in (out or "")


def extract_frames(video, out_dir, vid_id):
    """چند فریمِ یکنواخت از ویدیو می‌گیرد (عرض ۷۲۰ برای خواناییِ متنِ روی صفحه با مدلِ ویژن)، مسیرها را برمی‌گرداند."""
    os.makedirs(out_dir, exist_ok=True)
    dur = ffprobe_duration(video)
    frames = []
    if dur <= 0:
        # حداقل یک فریم از ثانیهٔ ۱
        outp = os.path.join(out_dir, f"{vid_id}_f01.jpg")
        rc, _, _ = run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "1",
                        "-i", video, "-frames:v", "1", "-vf", "scale=720:-1", "-y", outp], timeout=120)
        if rc == 0 and os.path.exists(outp):
            frames.append(outp)
        return frames
    # ⛔ ۱۴۰۵-۰۵-۲۱: نرخِ ثابت، بی‌سقف. تعداد فقط تابعِ طولِ ویدیوست.
    n = max(2, int(dur // FRAME_STEP_SECONDS) + 1)
    for i in range(n):
        # نقاطِ یکنواخت بینِ ۵٪ تا ۹۵٪
        t = dur * (0.05 + 0.90 * (i / max(1, n - 1)))
        outp = os.path.join(out_dir, f"{vid_id}_f{i+1:02d}.jpg")
        rc, _, _ = run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", video, "-frames:v", "1", "-vf", "scale=720:-1", "-y", outp], timeout=120)
        if rc == 0 and os.path.exists(outp):
            frames.append(outp)
    return frames


import re as _re
import tempfile as _tmp

# واترمارک را با «نام» نمی‌گیریم (برای هر سازنده فرق می‌کند) بلکه با «ثبات» می‌گیریم:
# متنِ واترمارک در همهٔ فریم‌ها تکرار می‌شود، ولی زیرنویس عوض می‌شود.
WATERMARK_FREQ = 0.60     # توکنی که در ≥۶۰٪ فریم‌ها دیده شود = واترمارک/لوگو، نه زیرنویس


def _strip_watermark(raw_frames):
    """توکن‌هایی که تقریباً در همهٔ فریم‌ها هستند را حذف می‌کند (لوگو/واترمارکِ ثابت).

    عمداً بدونِ نامِ برند: هر ویدیویی واترمارکِ خودش را دارد و لیستِ اسم جواب نمی‌دهد.
    """
    n = len(raw_frames)
    if n < 4:
        return raw_frames
    from collections import Counter
    c = Counter()
    for txt in raw_frames:
        c.update(set(txt.split()))
    stop = {w for w, k in c.items() if k / n >= WATERMARK_FREQ}
    return [" ".join(w for w in txt.split() if w not in stop).strip()
            for txt in raw_frames]


def _sim(a, b):
    A, B = set(a.split()), set(b.split())
    return len(A & B) / max(1, len(A | B))


_WHISPER = {"model": None, "loaded": False, "err": None}


def get_whisper():
    if _WHISPER["loaded"]:
        return _WHISPER["model"]
    _WHISPER["loaded"] = True
    try:
        from faster_whisper import WhisperModel
        _WHISPER["model"] = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    except Exception as e:
        _WHISPER["err"] = str(e)
        _WHISPER["model"] = None
    return _WHISPER["model"]


def media_duration(path):
    """طولِ مدیا به ثانیه با ffprobe؛ ناموفق بود None."""
    rc, out, _ = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", path], timeout=120)
    try:
        return float(out.strip()) if rc == 0 and out.strip() else None
    except ValueError:
        return None


def detect_language(model, video):
    """پاسِ اول — فقط تشخیصِ زبان، بدونِ pin و بدونِ پرامپت.

    faster-whisper پیش از تولیدِ متن تشخیص را انجام می‌دهد و آن را در info می‌گذارد،
    پس لازم نیست ژنراتورِ segments را مصرف کنیم؛ همان فراخوانِ سبک کافی است.
    """
    try:
        _segments, info = model.transcribe(
            video, language=None, beam_size=1, vad_filter=False, without_timestamps=True)
        lang = getattr(info, "language", None)
        prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        return lang, prob
    except Exception:
        return None, 0.0


def transcribe_full(video):
    """رونویسیِ کاملِ صوت — دو پاس: تشخیصِ زبان، سپس رونویسیِ pin‌شده به همان زبان.

    ⛔ سه درسِ قفل‌شده در این تابع جمع‌اند:
      ۱) ۱۴۰۵-۰۵-۰۷ — بدونِ beam search مدل هذیان می‌بافد؛ beam_size=5 می‌ماند.
      ۲) ۱۴۰۵-۰۵-۱۸ — pin کردنِ زبان روی "fa" هر صوتِ غیرفارسی را نابود می‌کند؛
         زبان باید تشخیص داده شود، نه فرض. pinِ env فقط فالبکِ کم‌اطمینانی است.
      ۳) ۱۴۰۵-۰۵-۱۸ — condition_on_previous_text باعثِ حلقهٔ تکرار در انتهای فایل می‌شد
         («از سلسلات از سلسلات از سلسلات…»)؛ خاموش شد.
    خروجی dict است تا لایهٔ بالا پوشش و اطمینانِ زبان را هم ببیند، نه فقط متن.
    """
    out = {"text": None, "lang": None, "lang_probability": None, "lang_source": None,
           "segments": [], "coverage": None, "duration": None, "error": None,
           "spoken_seconds": None}
    model = get_whisper()
    if model is None:
        out["error"] = _WHISPER.get("err") or "مدلِ whisper بارگذاری نشد"
        return out

    out["duration"] = media_duration(video)

    # ── پاسِ ۱: زبان چیست؟
    det_lang, det_prob = detect_language(model, video)
    pin = (WHISPER_LANG or "auto").strip().lower()
    if det_lang and det_prob >= LANG_MIN_PROB:
        lang, src = det_lang, "detected"
    elif pin and pin != "auto":
        lang, src = pin, "env-fallback"      # تشخیص مطمئن نبود → pinِ دستی
    else:
        lang, src = det_lang, "detected-low-confidence"
    out.update(lang=lang, lang_probability=round(det_prob, 3), lang_source=src)

    # ── پاسِ ۲: رونویسیِ کامل با زبانِ قطعی‌شده
    prompt = WHISPER_PROMPT or WHISPER_PROMPTS.get(lang or "", "") or None
    try:
        segments, info = model.transcribe(
            video,
            language=lang,                       # حالا زبانِ واقعی است، نه فرضِ فارسی
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,    # ضدِّ حلقهٔ تکرار
            initial_prompt=prompt,               # فقط پرامپتِ هم‌زبان
        )
        segs, spoken, parts = [], 0.0, []
        for seg in segments:
            txt = (seg.text or "").strip()
            if not txt:
                continue
            segs.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": txt})
            spoken += max(0.0, seg.end - seg.start)
            parts.append(txt)
        out["segments"] = segs
        out["spoken_seconds"] = round(spoken, 1)
        out["text"] = " ".join(parts).strip() or None
        if out["duration"]:
            out["coverage"] = round(min(1.0, spoken / out["duration"]), 3)
        if getattr(info, "language", None):
            out["lang"] = info.language
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def transcribe(video):
    """سازگاریِ عقب‌رو — (text, lang). جزئیات در transcribe_full است."""
    r = transcribe_full(video)
    if r.get("error") and not r.get("text"):
        return (f"[خطای رونویسی: {r['error']}]" if r["error"] != "مدلِ whisper بارگذاری نشد"
                else None), None
    return r.get("text"), r.get("lang")


def looks_like_mp4(path):
    """آیا فایل واقعاً ویدیوی MP4/MOV است؟ بر پایهٔ امضای بایتی، نه پسوند.

    ⚠️ درسِ ۱۴۰۵-۰۵-۰۴ (باگِ b3910): تشخیصِ ویدیو با پسوند شکننده است. نامِ بلندِ تلگرام هنگامِ
    کوتاه‌شدن پسوند را می‌بُرید و `.mp4` می‌شد `.mp`. وصلهٔ قبلی فقط `*.m` را به فهرست اضافه کرده
    بود — یعنی علامت را درمان کرده بود نه علت — و همین که بریدگی یک نویسه جلوتر افتاد، دوباره
    فایل جا ماند. ریشه در `telegram_watch.safe_name` اصلاح شد؛ این تابع لایهٔ دومِ دفاع است تا
    فایل‌های قدیمیِ بدنام هم گرفته شوند.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except Exception:
        return False
    # جعبهٔ ftyp در بایت‌های ۴ تا ۸ نشانهٔ خانوادهٔ ISO-BMFF (mp4/mov/m4v) است
    return len(head) >= 12 and head[4:8] == b"ftyp"


def enrich_key(key_dir):
    key = os.path.basename(key_dir.rstrip("/"))
    # تشخیصِ ویدیو دو مرحله‌ای: اول پسوندهای شناخته‌شده، بعد امضای بایتیِ هر فایلِ بی‌پسوند/بدپسوند.
    known_video = set(glob.glob(os.path.join(key_dir, "*.mp4")) +
                      glob.glob(os.path.join(key_dir, "*.mov")) +
                      glob.glob(os.path.join(key_dir, "*.m4v")))
    photo_ext = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    for cand in glob.glob(os.path.join(key_dir, "*")):
        if os.path.isdir(cand) or cand in known_video:
            continue
        if cand.lower().endswith(photo_ext) or cand.lower().endswith((".json", ".md", ".txt")):
            continue
        if looks_like_mp4(cand):
            print(f"  ! {os.path.basename(cand)} — پسوندش ویدیو نیست ولی محتوایش MP4 است؛ پردازش می‌شود.")
            known_video.add(cand)
    videos = sorted(known_video)
    photos = sorted(glob.glob(os.path.join(key_dir, "*.jpg")) +
                    glob.glob(os.path.join(key_dir, "*.jpeg")) +
                    glob.glob(os.path.join(key_dir, "*.png")))
    # عکس‌هایی که خودمان به‌عنوانِ فریم می‌سازیم را کنار بگذار
    photos = [p for p in photos if "/frames/" not in p.replace("\\", "/")]
    # ⛔ ۱۴۰۵-۰۵-۲۱: ورقهٔ تجمیعیِ فریم‌ها (frames_sheet.py) ساختهٔ خودمان است، نه محتوای بسته.
    # بی این فیلتر، بسته‌های ویدیویی «۱ عکس» گزارش می‌دادند و سیاههٔ داده غلط درمی‌آمد.
    photos = [p for p in photos if os.path.basename(p) != "frames-sheet.jpg"]

    frames_dir = os.path.join(key_dir, "frames")
    lines = [f"# رونوشت و فریم‌های بستهٔ {key}", ""]
    summary = {"key": key, "videos": [], "photos": len(photos),
               "whisper_model": WHISPER_MODEL, "frames_total": 0}
    lines.append("> منبعِ یگانهٔ متنِ روی‌صفحه **خوانشِ بصریِ فریم‌هاست**"
                 " (تصمیمِ کاربر ۱۴۰۵-۰۵-۲۱ — قفل: `onscreen-text-is-visual-only`).")
    lines.append("")

    if photos:
        lines.append(f"## عکس‌ها ({len(photos)})")
        lines.append("این‌ها خودشان اسلاید/فریم‌اند (کاروسل یا پستِ عکسی):")
        for p in photos:
            lines.append(f"- {os.path.basename(p)}")
        lines.append("")

    for v in videos:
        vid = os.path.basename(v).split("__")[0]
        vinfo = {"file": os.path.basename(v), "id": vid, "frames": 0,
                 "audio": False, "transcript_chars": 0, "lang": None}
        lines.append(f"## ویدیو {vid} — {os.path.basename(v)}")
        # فریم‌ها
        fr = extract_frames(v, frames_dir, vid)
        vinfo["frames"] = len(fr)
        summary["frames_total"] += len(fr)
        if fr:
            lines.append(f"فریم‌ها ({len(fr)}): " + ", ".join(os.path.basename(x) for x in fr))
        # رونویسی
        if has_audio_stream(v):
            vinfo["audio"] = True
            tr = transcribe_full(v)
            text, lang = tr.get("text"), tr.get("lang")
            if text and text.strip():
                vinfo["transcript_chars"] = len(text)
                vinfo["lang"] = lang
                vinfo["lang_probability"] = tr.get("lang_probability")
                vinfo["lang_source"] = tr.get("lang_source")
                vinfo["transcript_coverage"] = tr.get("coverage")
                vinfo["transcript_segments"] = len(tr.get("segments") or [])
                vinfo["audio_duration"] = tr.get("duration")
                vinfo["spoken_seconds"] = tr.get("spoken_seconds")
                vinfo["audio_verdict"] = "speech"
                # ⛔ گاردِ پوششِ صوت (۱۴۰۵-۰۵-۱۸): «رونویسی شد» کافی نیست؛ باید **همهٔ** صوت
                # شنیده شده باشد. پوششِ کم یعنی VAD بخشی را بلعیده یا مدل وسطِ کار بریده.
                cov = tr.get("coverage")
                low_cov = cov is not None and cov < MIN_TRANSCRIPT_COVERAGE
                vinfo["transcript_coverage_ok"] = (not low_cov)
                # رونوشتِ زمان‌دار کنارِ بسته می‌نشیند تا راستی‌آزماییِ کاملیِ صوت ممکن باشد.
                try:
                    with open(os.path.join(os.path.dirname(v), "transcript-segments.json"),
                              "w", encoding="utf-8") as fh:
                        json.dump({"lang": lang,
                                   "lang_probability": tr.get("lang_probability"),
                                   "lang_source": tr.get("lang_source"),
                                   "duration": tr.get("duration"),
                                   "spoken_seconds": tr.get("spoken_seconds"),
                                   "coverage": cov,
                                   "segments": tr.get("segments") or []},
                                  fh, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                lines.append("")
                _lp = tr.get("lang_probability")
                _src = {"detected": "خودتشخیص", "env-fallback": "فالبکِ env",
                        "detected-low-confidence": "خودتشخیصِ کم‌اطمینان"}.get(tr.get("lang_source"), "؟")
                lines.append(f"**رونوشتِ صوت** (زبان: {lang or '؟'} · اطمینان: {_lp} · روش: {_src}"
                             + (f" · پوشش: {cov}" if cov is not None else "") + "):")
                if low_cov:
                    lines.append("")
                    lines.append(f"> ⚠️ پوششِ رونویسی {cov} است (کمتر از {MIN_TRANSCRIPT_COVERAGE})"
                                 " — بخشی از صوت به متن نیامده. پیش از اتکا به این متن،"
                                 " `transcript-segments.json` را با طولِ ویدیو تطبیق بده.")
                lines.append("")
                lines.append(text)
            else:
                # ⚠️ درسِ ۱۴۰۵-۰۵-۰۴: این‌جا قبلاً همیشه «رونویسی انجام نشد» می‌نوشت، چه مدل
                # خراب بود چه ویدیو اصلاً گفتاری نداشت. آن پیام گمراه‌کننده بود: b3904 و b3912
                # فقط موسیقیِ پس‌زمینه دارند و متنشان روی تصویر است، ولی سند طوری می‌نوشت که
                # انگار ابزار شکسته. تشخیصِ نادرست باعث شد دنبالِ باگی بگردیم که وجود نداشت.
                # حالا سه حالت از هم جدا می‌شوند تا لایهٔ تحلیل بداند با چه چیزی طرف است.
                err = _WHISPER.get("err")
                lines.append("")
                if err:
                    vinfo["audio_verdict"] = "whisper_error"
                    lines.append(f"_(⛔ رونویسی شکست خورد — خطای ابزار: {err}. این نقصِ فنی است "
                                 "و باید پیگیری شود.)_")
                else:
                    vinfo["audio_verdict"] = "no_speech"
                    lines.append("_(ℹ️ گفتاری تشخیص داده نشد. ابزار سالم کار کرد ولی صوت "
                                 "گفتار نداشت — معمولاً یعنی ویدیو فقط موسیقیِ پس‌زمینه دارد و "
                                 "پیامش روی تصویر نوشته شده. این نقصِ فنی نیست؛ قضاوت را از "
                                 "روی فریم‌ها و متنِ روی تصویر بساز و همین را صریح اعلام کن.)_")
        else:
            vinfo["audio_verdict"] = "no_audio_stream"
            lines.append("_(بدونِ استریمِ صوتی — احتمالاً اسلایدِ طراحیِ صامت)_")
        lines.append("")
        summary["videos"].append(vinfo)

    if not videos and not photos:
        lines.append("_(مدیایی یافت نشد)_")

    with open(os.path.join(key_dir, "transcript.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(key_dir, "enrichment.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="_tgwork/watch", help="پوشهٔ ریشهٔ مدیای بسته‌ها")
    args = ap.parse_args()
    root = args.root
    if not os.path.isdir(root):
        print(f"[tg_enrich] پوشهٔ {root} نیست — چیزی برای غنی‌سازی نبود.")
        return
    keys = [d for d in sorted(glob.glob(os.path.join(root, "*")))
            if os.path.isdir(d) and os.path.basename(d) != "frames"]
    if not keys:
        print("[tg_enrich] هیچ بسته‌ای برای غنی‌سازی نبود.")
        return
    print(f"[tg_enrich] غنی‌سازیِ {len(keys)} بسته با مدلِ whisper={WHISPER_MODEL} …")
    for kd in keys:
        try:
            s = enrich_key(kd)
            nv = len(s["videos"]); tr = sum(1 for v in s["videos"] if v["transcript_chars"] > 0)
            print(f"  ✓ {s['key']}: {nv} ویدیو ({tr} رونویسی‌شده)، {s['photos']} عکس، {s['frames_total']} فریم")
        except Exception as e:
            print(f"  ⚠ {os.path.basename(kd)}: خطا در غنی‌سازی — {e}")


if __name__ == "__main__":
    main()
