#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日自動抓取全港購物優惠，寫成 deals/YYYY-MM-DD-shopping-deals.md。

設計原則（與 README「數字紀律」一致）：
1. 只收錄「原價 > 實價」而且可以喺同一頁 HTML 內核實嘅項目，唔估算、唔拼湊。
2. 每項都連去真實商品頁；上版前逐條驗 HTTP 200，死連結直接剔除。
3. 抓唔到嘅商戶／品類寫入「未收錄」，誠實申報，唔用假資料填數。
4. 已喺過去 N 日出現過嘅商品會跳過，令每日名單真係會轉。

用法：
    python scripts/fetch_deals.py              # 寫入今日檔案（已存在則跳過）
    python scripts/fetch_deals.py --dry-run    # 只印出，唔寫檔
    python scripts/fetch_deals.py --date 2026-11-02 --force
"""

from __future__ import annotations

import argparse
import gzip
import html
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEALS = ROOT / "deals"
HKT = timezone(timedelta(hours=8))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}

SOGO = "https://www.sogo.com.hk"
WELLCOME = "https://www.wellcome.com.hk"

# 每組：顯示名稱、配額（最多幾項）、來源清單
GROUPS = [
    {
        "title": "換季家品",
        "quota": 3,
        "pages": [
            (SOGO, "/tc/catalog/housewares-lifestyle/bedding"),
            (SOGO, "/tc/catalog/housewares-lifestyle/bedding/quilt"),
            (SOGO, "/tc/catalog/housewares-lifestyle/storage-organisation"),
        ],
    },
    {
        "title": "手袋鞋履",
        "quota": 2,
        "pages": [
            (SOGO, "/tc/catalog/fashion/women/bags-and-wallets"),
            (SOGO, "/tc/catalog/fashion/women/shoes"),
        ],
    },
    {
        "title": "美妝護膚（套裝價）",
        "quota": 2,
        "pages": [
            (SOGO, "/tc/catalog/beauty/best-value-set"),
            (SOGO, "/tc/catalog/beauty/skincare"),
        ],
    },
    {
        "title": "廚具家電",
        "quota": 1,
        "pages": [
            (SOGO, "/tc/catalog/housewares-lifestyle/kitchenware-tableware/wok-pan-pot"),
            (SOGO, "/tc/catalog/electrical-appliances/kitchen-appliances"),
        ],
    },
    {
        "title": "超市食品（入貨位）",
        "quota": 2,
        "pages": [
            (WELLCOME, "/zh-hant/wellcome/category/100022/1.html"),  # 零食
            (WELLCOME, "/zh-hant/wellcome/category/100020/1.html"),  # 米油麵
            (WELLCOME, "/zh-hant/wellcome/category/100002/1.html"),  # 飲品
            (SOGO, "/tc/catalog/freshmart/snacks-and-desserts/crisps"),
        ],
    },
]

LOOKBACK_DAYS = 14  # 同一件貨 14 日內不重複上榜
MIN_DISCOUNT = 0.05  # 至少 5% off 才算「優惠」
MAX_CARD_PAGES = 2  # SOGO 每組最多抓幾頁


@dataclass
class Item:
    merchant: str
    title: str
    url: str
    price: float
    original: float
    note: str = ""
    group: str = ""
    uid: str = ""
    src_page: str = ""

    @property
    def ratio(self) -> float:
        return self.price / self.original if self.original else 1.0

    @property
    def saving(self) -> float:
        return self.original - self.price


@dataclass
class Run:
    items: list[Item] = field(default_factory=list)
    failed: dict[str, list[str]] = field(default_factory=dict)  # 來源 -> 原因

    def fail(self, source: str, reason: str) -> None:
        self.failed.setdefault(source, []).append(reason)


# ---------------------------------------------------------------- fetch


def fetch(url: str, timeout: int = 25, retries: int = 2) -> str | None:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if attempt == retries:
                return f"__HTTP_{e.code}__"
        except Exception:
            if attempt == retries:
                return None
    return None


def link_ok(url: str) -> bool:
    body = fetch(url, timeout=20, retries=1)
    return isinstance(body, str) and not body.startswith("__HTTP_")


def money(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.]", "", text or "")
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


# ---------------------------------------------------------------- parsers

SOGO_CARD = re.compile(
    r'<div class="product-card">(.*?)(?=<div class="product-card">|<div class="pagination|</main>)',
    re.S,
)
SOGO_TITLE = re.compile(r'<div class="title">\s*(.*?)\s*</div>', re.S)
SOGO_HREF = re.compile(r'<a class="cover" href="([^"]+)"')
SOGO_DISC = re.compile(r'<div class="discount">\s*\$?\s*([\d.,]+)\s*</div>')
SOGO_REG = re.compile(r'<div class="regular">\s*\$?\s*([\d.,]+)\s*</div>')
SOGO_REMARK = re.compile(r'<div class="remarks[^"]*">\s*(.*?)\s*</div>', re.S)


def parse_sogo(body: str, group: str, run: Run) -> list[Item]:
    out: list[Item] = []
    for blk in SOGO_CARD.findall(body):
        href = SOGO_HREF.search(blk)
        title = SOGO_TITLE.search(blk)
        disc = SOGO_DISC.search(blk)
        reg = SOGO_REG.search(blk)
        if not (href and title and disc and reg):
            continue
        name = clean_text(title.group(1))
        price, original = money(disc.group(1)), money(reg.group(1))
        if not name or not price or not original:
            continue
        remark = clean_text(SOGO_REMARK.search(blk).group(1)) if SOGO_REMARK.search(blk) else ""
        uid = "/".join(filter(None, re.findall(r"/tc/product/([\d-]+)", href.group(1))))
        out.append(
            Item(
                merchant="崇光 SOGO 網店",
                title=name,
                url=SOGO + href.group(1),
                price=price,
                original=original,
                note=remark,
                group=group,
                uid=f"sogo:{uid or href.group(1)}",
            )
        )
    return out


WELLCOME_CARD = re.compile(r'<div class="ware-wrapper"[^>]*>(.*?)<div class="btn-wrapper', re.S)
WC_HREF = re.compile(r'href="(/zh-hant/wellcome/p/[^"]+)"')
WC_NAME = re.compile(r'<div class="promo"[^>]*>\s*(.*?)\s*</div>', re.S)
WC_LINE = re.compile(r'class="line-price"[^>]*>\s*([^<]*)<')
WC_CUR = re.compile(r'class="price"[^>]*>\s*\$?\s*([\d.,]+)\s*</div>', re.S)
WC_TAG = re.compile(r'class="price-tag"[^>]*>\s*(.*?)\s*</div>', re.S)


def parse_wellcome(body: str, group: str, run: Run) -> list[Item]:
    out: list[Item] = []
    for blk in WELLCOME_CARD.findall(body):
        href, name_m, line_m, cur_m = (
            WC_HREF.search(blk),
            WC_NAME.search(blk),
            WC_LINE.search(blk),
            WC_CUR.search(blk),
        )
        if not (href and name_m and line_m and cur_m):
            continue
        original, price = money(line_m.group(1)), money(cur_m.group(1))
        name = clean_text(name_m.group(1))
        if not name or not price or not original or original <= price:
            continue
        tag_m = WC_TAG.search(blk)
        tag = clean_text(tag_m.group(1)) if tag_m else ""
        uid = re.search(r"/i/(\d+)\.html", href.group(1))
        out.append(
            Item(
                merchant="惠康 Wellcome 網店",
                title=name,
                url=WELLCOME + href.group(1),
                price=price,
                original=original,
                note=tag,
                group=group,
                uid=f"wellcome:{uid.group(1) if uid else href.group(1)}",
            )
        )
    return out


def clean_text(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw or "")
    txt = html.unescape(txt)
    txt = txt.replace("\u200b", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", txt).strip()


# ---------------------------------------------------------------- pipeline


def seen_recently(today: date) -> set[str]:
    seen: set[str] = set()
    if not DEALS.exists():
        return seen
    cutoff = today - timedelta(days=LOOKBACK_DAYS)
    for f in DEALS.glob("*.md"):
        try:
            d = date.fromisoformat(f.stem.split("-shopping-deals")[0])
        except ValueError:
            continue
        if d < cutoff:
            continue
        text = f.read_text(encoding="utf-8")
        seen.update(f"sogo:{u}" for u in re.findall(r"/tc/product/([\d-]+)", text))
        seen.update(f"wellcome:{u}" for u in re.findall(r"/i/(\d+)\.html", text))
    return seen


def collect(today: date, run: Run, verbose: bool = True) -> tuple[list[Item], list[Item]]:
    skip = seen_recently(today)
    pool: dict[str, list[Item]] = {}
    for group in GROUPS:
        gname = group["title"]
        pool[gname] = []
        for base, path in group["pages"]:
            pages = [path]
            if base == SOGO:
                pages = [path] + [f"{path}?page={p}" for p in range(2, MAX_CARD_PAGES + 1)]
            for page_path in pages:
                url = base + page_path
                body = fetch(url)
                if body is None or body.startswith("__HTTP_"):
                    run.fail(host_of(url), f"{page_path} 抓取失敗")
                    continue
                parser = parse_sogo if base == SOGO else parse_wellcome
                found = parser(body, gname, run)
                for it in found:
                    it.src_page = url
                if verbose:
                    print(f"  · {host_of(url)}{page_path} → {len(found)} 項")
                if not found:
                    print(f"    ! {page_path} 冇解析到減價商品（可能全部正價或版面改動）")
                pool[gname].extend(found)

    picked: list[Item] = []
    for group in GROUPS:
        gname = group["title"]
        cands = [
            i
            for i in pool[gname]
            if i.uid not in skip
            and i.original > 0
            and (1 - i.ratio) >= MIN_DISCOUNT
            and i.price >= 20
            and not re.search(r"禮券|現金券|gift card", i.title, re.I)
        ]
        cands.sort(key=lambda i: (i.ratio, -i.saving))
        used_pages: set[str] = set()
        for item in cands:
            if len([p for p in picked if p.group == gname]) >= group["quota"]:
                break
            if item.title in {c.title for c in picked}:
                continue
            if item.src_page in used_pages:
                continue
            used_pages.add(item.src_page)
            picked.append(item)

    fresh = [
        i
        for g in GROUPS
        for i in pool[g["title"]]
        if i.uid not in skip and i.original > 0 and (1 - i.ratio) >= MIN_DISCOUNT and i.price >= 20
    ]
    return picked, fresh


def safe_url(url: str) -> str:
    """Markdown 連結內嘅括號／空格要編碼，否則會整斷連結。"""
    return url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url


def fill_to(items: list[Item], all_items: list[Item], target: int = 10) -> list[Item]:
    """配額不足 10 項時，用其餘最抵嘅項目補滿。"""
    if len(items) >= target:
        return items
    chosen = {i.uid for i in items}
    rest = sorted(
        [i for i in all_items if i.uid not in chosen and (1 - i.ratio) >= MIN_DISCOUNT],
        key=lambda i: i.ratio,
    )
    for item in rest:
        if len(items) >= target:
            break
        if item.title in {i.title for i in items}:
            continue
        items.append(item)
    return items


# ---------------------------------------------------------------- render


def fmt_money(v: float) -> str:
    return f"${v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"${int(v):,}"


def fmt_discount(ratio: float) -> str:
    if abs(ratio - 0.5) < 0.005:
        return "**半價**"
    return f"**{max(1, round(ratio * 100))}折**"


def render(items: list[Item], today: date, run: Run, stamp: datetime) -> str:
    weekday = "一二三四五六日"[today.weekday()]
    best_ratio = min(items, key=lambda i: i.ratio) if items else None
    best_saving = max(items, key=lambda i: i.saving) if items else None
    cheapest = min(items, key=lambda i: i.price) if items else None
    merchants = sorted({i.merchant for i in items})

    out: list[str] = []
    out.append(f"# 購物優惠日誌｜{today.year}年{today.month}月{today.day}日（{weekday}）\n")
    out.append(
        f"> 核實時間：{stamp.strftime('%Y-%m-%d %H:%M')} HKT｜**{len(items)} 項**由官網即時頁面抓取，"
        "貨品名可點擊直達商品頁\n"
        f"> 今日來源：**{'、'.join(merchants)}**｜自動更新：每晚 21:00 HKT\n"
    )
    out.append("---\n")

    # 今日總結
    summary = ""
    if items:
        summary = (
            f"今日 {len(items)} 項優惠。折扣最狠：{best_ratio.title} {fmt_money(best_ratio.price)}"
            f"（{fmt_discount(best_ratio.ratio).strip('*')}，原價 {fmt_money(best_ratio.original)}）；"
            f"入場價最低：{cheapest.title} {fmt_money(cheapest.price)}；"
            f"單項慳得最多：{best_saving.title}（慳 {fmt_money(best_saving.saving)}）。"
        )
    else:
        summary = "今日未能從官網核實任何優惠，已按下不發佈，等下一輪自動更新。"
    out.append("## 今日總結\n")
    out.append(summary + "\n")
    out.append("---\n")

    # 分組表格
    table_head = (
        "| # | 商戶 | 貨品（點擊查價） | 實價 | 原價 | 折扣 | 期限 / 方式 |\n"
        "|---|------|------|------|------|------|------------|"
    )
    idx = 0
    for group in GROUPS:
        rows = [i for i in items if i.group == group["title"]]
        if not rows:
            continue
        out.append(f"## {group['title']}\n")
        out.append(table_head)
        for item in rows:
            idx += 1
            note = item.note or "官網特價"
            channel = "網店" if "網店" in item.merchant else "門市／網店"
            out.append(
                f"| {idx} | {item.merchant} | [{item.title}]({safe_url(item.url)}) | "
                f"{fmt_money(item.price)} | {fmt_money(item.original)} | "
                f"{fmt_discount(item.ratio)} | {note}｜{channel} |"
            )
        out.append("")

    # 三個最抵
    top = sorted(items, key=lambda i: i.saving, reverse=True)[:3]
    if top:
        out.append("## 今晚三個最抵\n")
        for n, item in enumerate(top, 1):
            out.append(
                f"{n}. **{item.title} {fmt_money(item.price)}"
                f"（{fmt_discount(item.ratio).strip('*')}，慳 {fmt_money(item.saving)}）** — "
                f"[{item.merchant}]({safe_url(item.url)})"
            )
        out.append("")

    # 未收錄
    out.append("## 未收錄（誠實申報）\n")
    lines: list[str] = []
    for source, reasons in run.failed.items():
        uniq = sorted(set(reasons))
        lines.append(f"- **{source}**：{'；'.join(uniq[:3])}，今日未收錄。")
    lines.append(
        "- **百佳／屈臣氏／豐澤／一田／HKTVmall**：官網對自動抓取回 403 或需要 JS 載入，"
        "抓唔到可核實價格，故不列入，不以估算代替。"
    )
    lines.append("- **餐飲、信用卡疊加優惠**：需逐間門市或銀行條款核實，自動化未覆蓋，暫不列出。")
    out.extend(lines)
    out.append("")

    # 來源：列出實際用過嘅頁面
    out.append("## 資料來源\n")
    for page in sorted({i.src_page for i in items}):
        out.append(f"- {page}")
    out.append(f"- 本頁由 `scripts/fetch_deals.py` 於 {stamp.strftime('%Y-%m-%d %H:%M')} HKT 自動產生。")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定日期 YYYY-MM-DD（預設今日 HKT）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="即使今日檔案已存在都重寫")
    args = ap.parse_args()

    stamp = datetime.now(HKT)
    today = date.fromisoformat(args.date) if args.date else stamp.date()
    target = DEALS / f"{today.isoformat()}-shopping-deals.md"

    if target.exists() and not args.force and not args.dry_run:
        print(f"{target.name} 已存在，跳過（用 --force 可重寫）")
        return 0

    print(f"抓取 {today} 優惠…")
    run = Run()
    picked, candidates = collect(today, run)
    picked = fill_to(picked, candidates)
    print(f"初選 {len(picked)} 項；逐條驗連結…")

    verified: list[Item] = []
    for item in picked:
        if link_ok(item.url):
            verified.append(item)
        else:
            run.fail(item.merchant, "商品連結打唔開，已剔除")
    print(f"連結核實後 {len(verified)} 項")

    if not verified:
        print("沒有可核實優惠，今日不發佈。", file=sys.stderr)
        return 1

    md = render(verified, today, run, stamp)
    if args.dry_run:
        print("\n" + "=" * 60 + "\n" + md)
        return 0

    DEALS.mkdir(exist_ok=True)
    target.write_text(md, encoding="utf-8")
    print(f"已寫入 {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
