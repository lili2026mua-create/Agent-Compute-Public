#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""interpretation_contract.py — قراردادِ خروجیِ تفسیر را می‌خواند و بسته را با آن می‌سنجد.

منبعِ حقیقتِ فهرستِ خروجی: Agents-General/skill-trainer/mapping-specialist/output-contract.yaml
هم video-interpreter (تولیدکننده) و هم mapping-specialist (نمایش‌گر) از همین فایل تصمیم می‌گیرند؛ این
اسکریپت همان فهرست را برنامه‌ای در دسترس می‌گذارد تا هیچ لیستِ موازیِ هاردکدی لازم نباشد.

کاربرد:
  python3 Sys.scripts/interpretation_contract.py --list [--for final|preview|both]
      فهرستِ آیتم‌های قرارداد (کلید | فایل | تولیدکننده | required_for) را چاپ می‌کند.
  python3 Sys.scripts/interpretation_contract.py --check <پوشهٔ interpretation> [--stage final|preview]
      وجودِ فایلِ هر آیتمِ produced_by=video-interpreter که برای این stage لازم است را می‌سنجد.
      stage=final (پیش‌فرض): آیتم‌های both+final. stage=preview: فقط both.
خروج: 0 کامل · 1 کمبود · 2 ورودیِ نادرست.

بدونِ وابستگیِ بیرونی: yamlِ سادهٔ همین فایل را با پارسرِ کوچک می‌خواند (ساختارش تخت و کنترل‌شده است).
"""
import os
import sys

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Agents-General/skill-trainer/mapping-specialist/output-contract.yaml",
)


def load_items(path):
    """پارسرِ کوچکِ همین قالبِ مشخص: بلوک‌های '  - key:' با فیلدهای دوفاصله‌ای."""
    items = []
    cur = None
    in_items = False
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if line.strip().startswith("#") or not line.strip():
            continue
        if line.strip() == "items:":
            in_items = True
            continue
        if not in_items:
            continue
        if line.lstrip().startswith("- key:"):
            if cur:
                items.append(cur)
            cur = {"key": line.split(":", 1)[1].strip()}
        elif cur is not None and ":" in line and line.startswith("    "):
            k, v = line.strip().split(":", 1)
            cur[k.strip()] = v.strip().split("#", 1)[0].strip()
    if cur:
        items.append(cur)
    return items


def main():
    argv = sys.argv[1:]
    if "--list" in argv:
        want = None
        if "--for" in argv:
            want = argv[argv.index("--for") + 1]
        for it in load_items(CONTRACT):
            if want and it.get("required_for") not in (want, "both"):
                continue
            print(f"{it['key']:24} | {it.get('file',''):46} | {it.get('produced_by',''):18} | {it.get('required_for','')}")
        return 0

    if "--check" in argv:
        pkg = argv[argv.index("--check") + 1]
        stage = "final"
        if "--stage" in argv:
            stage = argv[argv.index("--stage") + 1]
        need = {"both"} if stage == "preview" else {"both", "final"}
        if not os.path.isdir(pkg):
            print(f"✗ پوشهٔ بسته یافت نشد: {pkg}", file=sys.stderr)
            return 2
        missing = []
        for it in load_items(CONTRACT):
            if it.get("produced_by") != "video-interpreter":
                continue
            if it.get("required_for") not in need:
                continue
            f = (it.get("file") or "").split("(")[0].split("+")[0].strip()
            base = f.split("interpretation/", 1)[-1]
            path = os.path.join(pkg, base)
            # media = پوشه یا هر فایلِ نماینده؛ بقیه = فایلِ مشخص
            ok = os.path.exists(path) or os.path.exists(os.path.join(pkg, base.rstrip("/")))
            if not ok:
                missing.append((it["key"], base))
        if missing:
            for k, b in missing:
                print(f"✗ {k} — فایلِ «{b}» نیست (video-interpreter این آیتمِ قرارداد را نساخته)")
            print("—", file=sys.stderr)
            print(f"⛔ بسته برای stage={stage} کامل نیست؛ video-interpreter باید آیتم‌های بالا را بسازد.", file=sys.stderr)
            return 1
        print(f"✅ بسته برای stage={stage} کاملِ قرارداد است.")
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
