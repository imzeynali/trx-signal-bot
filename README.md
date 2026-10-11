# 📊 Spot Signal Web

وب‌اپ تحلیل و سیگنال اسپات ارز دیجیتال (تایم‌فریم ۱ ساعته): ارز دلخواه را وارد کنید، چارت و اخبار را ببینید،
پیشنهاد خرید/فروش با **حد ضرر و ۳ تارگت** بگیرید و استراتژی را با پارامترهای دلخواه **بک‌تست** کنید.

> ⚠️ فقط آموزشی/آماری است و توصیه مالی نیست.

## ساختار
```
core/        هسته: market (ccxt) · news (RSS+سنتیمنت) · strategy (اندیکاتور/امتیاز/سیگنال) · backtest
web/app.py   بک‌اند FastAPI  (/api/symbols  /api/analyze  /api/backtest  /api/docs)
web/static/  رابط کاربری (HTML/JS + TradingView Lightweight Charts، بدون نیاز به CDN)
telegram_bot.py   ربات تلگرام اختیاری (همان هسته)
```

## اجرای محلی
```bash
python -m venv venv && source venv/bin/activate      # ویندوز: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # و EXCHANGE / APP_PASSWORD را تنظیم کنید
uvicorn web.app:app --host 0.0.0.0 --port 8000
```
مرورگر: http://localhost:8000

## تست فقط با گیتهاب (Codespaces)
Code → Codespaces → Create. محیط خودش پکیج‌ها را نصب می‌کند (با `EXCHANGE=kucoin`). سپس:
```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000
```
پورت ۸۰۰۰ خودکار فوروارد و در مرورگر باز می‌شود.

## استقرار روی سرور (Ubuntu)
```bash
sudo apt update && sudo apt install -y python3-venv git nginx
sudo useradd -m botuser
sudo git clone https://github.com/USERNAME/REPO.git /opt/signal_web
sudo chown -R botuser:botuser /opt/signal_web && cd /opt/signal_web
sudo -u botuser python3 -m venv venv && sudo -u botuser venv/bin/pip install -r requirements.txt
sudo -u botuser cp .env.example .env && sudo -u botuser nano .env   # EXCHANGE و APP_PASSWORD
sudo cp signalweb.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now signalweb
```
Nginx (`/etc/nginx/sites-available/signal`):
```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host;
                 proxy_set_header X-Forwarded-For $remote_addr; proxy_read_timeout 120s; }
}
```
سپس `sudo ln -s /etc/nginx/sites-available/signal /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
و برای HTTPS: `sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx -d your-domain.com`.

**Docker:** `docker compose up -d --build`

آپدیت: `cd /opt/signal_web && sudo -u botuser git pull && sudo systemctl restart signalweb`

## امنیت
- روی سرور عمومی حتماً `APP_PASSWORD` بگذارید (Basic Auth) و از HTTPS استفاده کنید.
- فایل `.env` هرگز کامیت نمی‌شود (در `.gitignore` است).

## منطق استراتژی
امتیاز تکنیکال (EMA200، تقاطع EMA20/50، MACD، RSI، بولینگر، حجم) + تعدیل اخبار (±۲) → اگر امتیاز ≥ آستانه خرید و روند
روزانه صعودی باشد: خرید؛ اگر ≤ آستانه خروج باشد: خروج. حد ضرر = ضریب ATR × ATR و تارگت‌ها ۱R/۲R/۳R.
بک‌تست: ورود روی close کندل سیگنال، کارمزد دوطرفه، اگر در یک کندل هم SL و هم TP لمس شود SL فرض می‌شود.
