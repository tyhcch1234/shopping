# 全港購物優惠日誌

每晚 **10 項**全港最抵優惠：**超市・網購・商場・餐飲・家電**，一日一份，向下累積。每項貨品名都係超連結，可直達來源頁查價／領券。

- 網站：https://tyhcch1234.github.io/shopping/
- 每日檔案：`deals/YYYY-MM-DD-shopping-deals.md`

## 結構

| 章節 | 內容 |
|---|---|
| 今日總結 | 一句講清今日主場、最平折扣、慳得最多邊項 |
| 1–3 換季家品 | 床品、被鋪、家居 |
| 4–5 手袋 | 半價手袋、鞋履 |
| 6–7 美妝護膚 | 套裝價、送禮門檻 |
| 8 廚具 | 鍋具、廚房電器 |
| 9–10 食品 / 日用 | 超市級入貨位、買一送一 |
| 疊加著數 | 信用卡／支付平台／會員額外優惠（不佔 10 項） |
| 今晚三個最抵 | 折扣＋實際慳幾多 |
| 未收錄 | 當日抓唔到嘅來源，誠實申報 |
| 資料來源 | 每份都列可追溯頁面 |

## 每項必備

1. 商戶名
2. 貨品名（**Markdown 超連結直達商品頁或優惠頁**）
3. 實價、原價、折扣率（實價 ÷ 原價）
4. 期限與購買方式（網店／門市／店取）

## 新增一日

```bash
bash scripts/publish.sh /path/to/2026-11-02-shopping-deals.md
```

腳本會複製檔案入 `deals/`、重建 `index.md`、commit 並 push。
Push 到 `deals/` 時，GitHub Actions（`.github/workflows/build-index.yml`）亦會自動重建首頁。

## 更新節奏（自動）

每晚 **21:00 HKT** 由 GitHub Actions（`.github/workflows/daily-deals.yml`）自動跑 `scripts/fetch_deals.py`：

1. 抓崇光網店 5 個品類分類頁（家品／手袋／美妝／廚具／食品）同惠康網店食品分類頁，每頁抽「實價 < 原價」嘅商品。
2. 逐條驗證商品連結回 200，死連結即剔除；同一件貨 14 日內不重複上榜。
3. 按折扣排序、每組取配額（3／2／2／1／2），寫入 `deals/YYYY-MM-DD-shopping-deals.md`，重建 `index.md` 並 push。
4. 抓唔到嘅商戶寫入「未收錄」，唔會用估算或舊價填數；如果全日零項可核實，job 會失敗而**不發佈**假內容。

手動補跑：Actions → 「每日優惠更新」→ Run workflow。也可以本機：

```bash
python3 scripts/fetch_deals.py --dry-run   # 先預覽
python3 scripts/fetch_deals.py --date 2026-11-05 --force
```

要加新商戶，改 `scripts/fetch_deals.py` 頂部 `GROUPS`（加 URL + 一個 parser）即可。目前只覆蓋伺服器端輸出價格嘅網站；百佳、屈臣氏、豐澤、一田、HKTVmall 會回 403 或依賴 JS，暫未收錄。

## 數字紀律

所有實價、原價、折扣率、期限必須可追溯至當日開啟過的商戶頁面或官方活動頁。查不到就寫「未收錄」，不以估算、舊價或傳聞代替；折扣率一律以實價 ÷ 原價計算並四捨五入至整折。所有連結上版前都要實測可開（HTTP 200 或瀏覽器可正常載入）。
