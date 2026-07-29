# 台股籌碼雷達

台積電供應鏈 27 檔觀察股的籌碼／成長／估值篩選工具。網站：https://a217443-sudo.github.io/tw-stock-chip-radar/

- `index.html` — 完整網站（單一檔案，無外部依賴）
- `scripts/daily_update.py` — 每個交易日收盤後，從台灣證券交易所（TWSE）與證券櫃買中心（TPEx）公開資料 API 抓取最新股價與三大法人買賣超，更新 `index.html` 裡的資料
- `.github/workflows/daily-update.yml` — 週一到週五台北時間 17:00 自動執行上述腳本並推送更新（GitHub Actions，免費額度內）

EPS 預估、毛利率、股本等基本面數字不在每日自動更新範圍內（公開資料沒有低成本的每日來源），如需更新請直接編輯 `index.html` 裡對應股票的欄位。

本工具僅做資料整理與量化排序，不構成投資建議。
