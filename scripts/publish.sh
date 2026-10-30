#!/usr/bin/env bash
# 把一日優惠加入 repo、重建首頁、commit 並 push。
# 用法：bash scripts/publish.sh /path/to/2026-11-02-shopping-deals.md
set -euo pipefail

SRC="${1:?用法: publish.sh <deals.md>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(basename "$SRC")"
DATE="${NAME%%-shopping-deals.md}"

cp "$SRC" "$REPO/deals/$NAME"
cd "$REPO"
python3 scripts/build_index.py
git add -A
if git diff --cached --quiet; then
  echo "沒有變更，無需 push（$DATE）"
  exit 0
fi
git commit -q -m "優惠：$DATE"
git push -q origin main
echo "已發佈 $DATE -> https://tyhcch1234.github.io/shopping/"
