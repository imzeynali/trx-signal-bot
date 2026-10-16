# 🤖 ربات تلگرام معامله اسپات

ربات تلگرامی برای **بازار اسپات** (فقط خرید/فروش، بدون اهرم و شورت) با منوی دکمه‌شیشه‌ای:

| دکمه | کار |
|---|---|
| 📊 بک‌تست استراتژی | ارز ← استراتژی (۶ گزینه) ← تایم‌فریم (1H / 4H) ← نتیجه بک‌تست یک‌ساله |
| 🚀 شروع معامله | همان مراحل ← تحلیل لحظه‌ای ← تایید ← باز شدن **معامله شبیه‌سازی‌شده** با حد ضرر و ۳ تارگت |
| 📈 گزارش سود و زیان | سود/زیان امروز، ۷ روز، ۳۰ روز، ۳۶۵ روز و کل + معاملات باز با امکان بستن دستی |
| 💡 پیشنهاد معاملاتی | همان مراحل ← خرید/صبر/خروج + ورود، حد ضرر، تارگت‌ها، اخبار و آمار بک‌تست |

> «شروع معامله» **شبیه‌سازی (Paper Trading)** است؛ هیچ پولی جابه‌جا نمی‌شود و به صرافی کلید API داده نمی‌شود.
> فقط آموزشی/آماری است و توصیه مالی نیست.

## استراتژی‌ها
امتیازی چندشاخصه · کراس EMA20/50 · کراس MACD · بازگشت RSI · بازگشت بولینگر · شکست کانال دانچین

مدیریت معامله (برای همه): حد ضرر = ۱.۵×ATR؛ تارگت‌ها ۱R/۲R/۳R و هر کدام ⅓ حجم؛
پس از تارگت ۱ حد ضرر به نقطه ورود می‌رود؛ کارمزد ۰.۱٪ هر سمت.
اگر در یک کندل هم حد ضرر و هم تارگت لمس شود، حد ضرر فرض می‌شود (محافظه‌کارانه).

## راه‌اندازی

### ۱) ساخت ربات
در تلگرام با @BotFather دستور `/newbot` را بزنید و توکن را بگیرید. آیدی عددی خودتان را هم از @userinfobot بگیرید.

### ۲) نصب روی VPS (Ubuntu)
```bash
sudo apt update && sudo apt install -y python3-venv git
sudo useradd -m -s /bin/bash botuser
sudo git clone https://github.com/USERNAME/REPO.git /opt/spot_bot
sudo chown -R botuser:botuser /opt/spot_bot && cd /opt/spot_bot

sudo -u botuser python3 -m venv venv
sudo -u botuser venv/bin/pip install -r requirements.txt
sudo -u botuser cp .env.example .env
sudo -u botuser nano .env          # BOT_TOKEN ، ALLOWED_USERS ، EXCHANGE

sudo cp spotbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spotbot
```
بررسی: `sudo systemctl status spotbot` و لاگ زنده: `journalctl -u spotbot -f`

(اگر ریپو Private است، به‌جای clone ساده از Deploy Key یا توکن فقط‌خواندنی استفاده کنید.)

### ۳) Docker (جایگزین)
```bash
cp .env.example .env && nano .env
docker compose up -d --build
docker compose logs -f
```

### آپدیت
```bash
cd /opt/spot_bot && sudo -u botuser git pull && sudo systemctl restart spotbot
```

### بکاپ معاملات
فایل `data/trades.db` را کپی کنید.

## نکات مهم
- **موقعیت سرور:** Binance از IPهای آمریکا بلاک است. VPS را در اروپا/آسیا بگیرید یا `EXCHANGE=kucoin` بگذارید.
- **امنیت:** حتماً `ALLOWED_USERS` را پر کنید؛ در غیر این صورت هر کسی ربات را پیدا کند می‌تواند از آن (و سهمیه API صرافی) استفاده کند.
- **توکن ربات** را فقط در `.env` نگه دارید و هرگز در گیت یا چت نگذارید.
- بک‌تست اخبار را لحاظ نمی‌کند؛ اخبار فقط در «پیشنهاد» به‌صورت هشدار نمایش داده می‌شود.
- پایش معاملات شبیه‌سازی‌شده هر ۶۰ ثانیه با کندل‌های ۱ دقیقه‌ای انجام می‌شود و اگر ربات خاموش باشد، پس از روشن شدن عقب‌ماندگی را جبران می‌کند.

## ساختار
```
bot.py            ربات و منوها
core/market.py    داده بازار (ccxt)         core/strategies.py   ۶ استراتژی
core/engine.py    مدیریت پوزیشن (SL/TP)     core/backtest.py     بک‌تست و آمار
core/suggest.py   پیشنهاد معاملاتی          core/paper.py        معامله شبیه‌سازی + گزارش
core/news.py      اخبار RSS + سنتیمنت       core/indicators.py   اندیکاتورها
```
