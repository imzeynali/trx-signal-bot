import os

from dotenv import load_dotenv

load_dotenv()

EXCHANGE = os.getenv("EXCHANGE", "binance")      # binance / kucoin / okx / mexc ...
APP_USER = os.getenv("APP_USER", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")     # اگر خالی نباشد، سایت رمزدار می‌شود (Basic Auth)
TIMEFRAME = "1h"                                  # طبق نیاز پروژه ثابت است
