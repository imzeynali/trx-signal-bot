# TRX Spot Signal Telegram Bot

ربات تلگرام برای سیگنال اسپات ارز دیجیتال (شروع با TRX/USDT): تحلیل تکنیکال + اخبار + فیلتر روند روزانه،
حد ضرر و تارگت بر پایه ATR، و آمار بک‌تست روزانه/ماهانه/سالانه.

> ⚠️ فقط آموزشی است و توصیه مالی نیست.

## اجرا
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # BOT_TOKEN و ADMIN_ID را پر کنید
python bot.py
```

## اجرا با Docker
```bash
docker build -t trxbot .
docker run -d --name trxbot --restart unless-stopped --env-file .env -v $(pwd)/data:/app/data trxbot
```

## دستورات
/signal /stats /news /list /add /remove /subscribe /unsubscribe
