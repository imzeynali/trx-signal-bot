import numpy as np

from . import market
from .backtest import run_backtest
from .config import ATR_MULT, TARGETS_R, TF, WARMUP_DAYS
from .indicators import add_indicators
from .news import fetch_news
from .strategies import STRATEGIES


def backtest_for(symbol: str, key: str, tf: str) -> dict:
    cfg = TF[tf]
    df = market.fetch_ohlcv(symbol, tf, cfg["backtest_days"] + WARMUP_DAYS, ttl=900)
    if len(df) < 24 * (WARMUP_DAYS + 20) / (4 if tf == "4h" else 1):
        raise ValueError("تاریخچه این ارز برای بک‌تست کافی نیست (ارز تازه‌لیست‌شده؟).")
    return run_backtest(df, key, tf)


def build_suggestion(symbol: str, key: str, tf: str, with_stats: bool = True) -> dict:
    cfg, strat = TF[tf], STRATEGIES[key]
    df = market.fetch_ohlcv(symbol, tf, cfg["live_days"], ttl=45)
    if len(df) < 260:
        raise ValueError("داده کافی برای تحلیل این ارز وجود ندارد.")
    d = add_indicators(df)
    entry, exit_ = strat.fn(d)
    n = len(d)
    window = range(n - 3, n - 1)                       # دو کندل بسته‌شده اخیر
    last_in = max((i for i in window if entry[i]), default=-1)
    last_out = max((i for i in window if exit_[i]), default=-1)

    row = d.iloc[-2]
    price, atr = float(d["close"].iloc[-1]), float(row.atr)
    risk = atr * ATR_MULT
    action, note = "WAIT", "سیگنال تازه‌ای از این استراتژی وجود ندارد؛ منتظر بمانید."
    if last_in > last_out:
        if price - float(d["close"].iloc[last_in]) > 0.75 * atr:
            note = "سیگنال خرید صادر شده بود اما قیمت از نقطه سیگنال زیاد فاصله گرفته؛ ورود دیرهنگام است."
        else:
            action, note = "BUY", "سیگنال خرید تازه از این استراتژی صادر شده است."
    elif last_out > last_in:
        action, note = "SELL", "سیگنال خروج صادر شده؛ خرید جدید توصیه نمی‌شود و اگر پوزیشن دارید خروج/کاهش را بررسی کنید."

    levels = {"entry": price, "stop_loss": price - risk, "stop_pct": -risk / price * 100,
              "targets": [{"r": r, "price": price + risk * r, "pct": risk * r / price * 100} for r in TARGETS_R]}

    news = fetch_news(symbol)
    warn = bool(news["asset_count"] >= 2 and news["asset_sent"] < -0.3) or bool(news["market_count"] >= 3 and news["market_sent"] < -0.4)

    stats = None
    if with_stats:
        try:
            stats = backtest_for(symbol, key, tf)["stats"]
        except Exception:
            stats = None
    return {"symbol": symbol, "key": key, "tf": tf, "action": action, "note": note, "price": price,
            "levels": levels, "risk_pct": risk / price * 100, "rsi": float(row.rsi),
            "atr_pct": atr / price * 100, "ema200_up": bool(row.close > row.ema200),
            "ema_up": bool(row.ema20 > row.ema50), "news": news, "news_warning": warn, "stats": stats}
