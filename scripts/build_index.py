#!/usr/bin/env python3
"""Scan deals/*.md and rebuild index.md (newest first).

Each daily file must contain a "## 今日總結" section; its first paragraph is
used as the index summary. The date is taken from the filename prefix
YYYY-MM-DD. Item count is read from the "## 今日總結" block header if present,
otherwise counted from numbered table rows.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEALS = ROOT / "deals"
OUT = ROOT / "index.md"

FRONT_MATTER = """---
title: 全港購物優惠日誌
---

"""

HEADER = """# 全港購物優惠日誌

每晚 20 項全港最抵優惠：超市・網購・商場・餐飲・家電。一日一份，向下累積。

"""


def summary_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"##\s*今日總結\s*\n+(.+?)\n\s*(?:---|\n##)", text, re.S)
    if not m:
        return ""
    block = m.group(1).strip()
    block = re.sub(r"\s*\n\s*", "", block)
    block = block.replace("**", "")
    block = block.replace("\\$", "$")
    return block


def item_count(path: Path) -> int:
    """Count highest leading index in deal tables (1..N)."""
    text = path.read_text(encoding="utf-8")
    nums = [int(n) for n in re.findall(r"^\|\s*(\d{1,3})\s*\|", text, re.M)]
    return max(nums) if nums else 0


def main() -> None:
    files = sorted(DEALS.glob("*.md"), reverse=True)
    lines = [FRONT_MATTER, HEADER]
    for f in files:
        date = f.stem.split("-shopping-deals")[0]
        href = f"{f.parent.name}/{f.stem}.html"
        n = item_count(f)
        badge = f"　`{n} 項`" if n else ""
        lines.append(f"## [{date}]({href}){badge}\n")
        s = summary_of(f)
        if s:
            lines.append(f"{s}\n")
        lines.append("")
    if not files:
        lines.append("_尚未有優惠日誌。_\n")
    lines.append("---\n")
    lines.append(f"共 {len(files)} 日紀錄。\n")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"index.md rebuilt with {len(files)} day(s)")


if __name__ == "__main__":
    main()
