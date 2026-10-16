import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
EXCHANGE = os.getenv("EXCHANGE", "binance")          # binance / kucoin / okx / mexc ...
# آیدی عددی کاربران مجاز (جدا شده با کاما). خالی = همه می‌توانند استفاده کنند
ALLOWED_USERS = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x.isdigit()}
DB_PATH = os.getenv("DB_PATH", "data/trades.db")
PAPER_TRADE_SIZE = float(os.getenv("PAPER_TRADE_SIZE", "100"))   # سرمایه مجازی هر معامله (USDT)
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "5"))
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "60"))       # ثانیه

FEE = 0.001                    # کارمزد 0.1% هر سمت (اسپات)
ATR_MULT = 1.5                 # فاصله حد ضرر = 1.5 × ATR  (برابر 1R)
TARGETS_R = (1.0, 2.0, 3.0)    # تارگت‌ها بر حسب R
FRACS = (1 / 3, 1 / 3, 1 / 3)  # سهم حجم هر تارگت
WARMUP_DAYS = 60

# فقط دو تایم‌فریم مجاز است
TF = {
    "1h": {"label": "1H", "ms": 3_600_000, "backtest_days": 365, "live_days": 90, "max_hold": 72},
    "4h": {"label": "4H", "ms": 14_400_000, "backtest_days": 365, "live_days": 200, "max_hold": 36},
}
