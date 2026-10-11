from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import market
from .news import fetch_news, news_adjustment
from .util import to_epoch

TARGETS_R = (1.0, 2.0, 3.0)     # تارگت‌های سیگنال بر حسب R (R = فاصله حد ضرر)
WARMUP_DAYS = 60                # دوره گرم‌شدن اندیکاتورها قبل از شروع بک‌تست
CHART_CANDLES = 400

LABELS = {
    "trend_ema200": "روند بلندمدت (قیمت نسبت به EMA200)",
    "ema_cross": "تقاطع EMA20 / EMA50",
    "macd": "مومنتوم MACD",
    "rsi": "RSI",
    "bollinger": "باندهای بولینگر",
    "volume": "حجم معاملات",
}


@dataclass
class Params:
    buy_threshold: float = 4.0     # حداقل امتیاز برای خرید
    sell_threshold: float = -3.0   # حداکثر امتیاز برای خروج
    atr_mult: float = 1.5          # فاصله حد ضرر = atr_mult × ATR
    tp_r: float = 2.0              # تارگت خروج در بک‌تست (بر حسب R)
    fee: float = 0.001             # کارمزد هر سمت
    max_hold: int = 72             # حداکثر کندل نگهداری در بک‌تست
    daily_filter: bool = True      # خرید فقط در روند روزانه صعودی


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l = df["close"], df["high"], df["low"]
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()

    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    df["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()

    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    sma, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["bb_up"], df["bb_lo"] = sma + 2 * sd, sma - 2 * sd
    df["vol_ma"] = df["volume"].rolling(20).mean()
    return df


def compute_components(d: pd.DataFrame) -> pd.DataFrame:
    """امتیاز هر اندیکاتور؛ جمع آن‌ها امتیاز تکنیکال (حدود -8 تا +8)"""
    comp = pd.DataFrame(index=d.index)
    comp["trend_ema200"] = np.where(d.close > d.ema200, 2, -2)
    comp["ema_cross"] = np.where(d.ema20 > d.ema50, 1, -1)
    rising = d.macd_hist > d.macd_hist.shift()
    comp["macd"] = np.where((d.macd_hist > 0) & rising, 1, np.where((d.macd_hist < 0) & ~rising, -1, 0))
    comp["rsi"] = np.select([d.rsi < 30, d.rsi < 45, d.rsi > 75, d.rsi > 65], [2, 1, -2, -1], 0)
    comp["bollinger"] = np.where(d.close < d.bb_lo, 1, np.where(d.close > d.bb_up, -1, 0))
    strong = d.volume > 1.2 * d.vol_ma
    comp["volume"] = np.where(strong & (d.close > d.open), 1, np.where(strong & (d.close < d.open), -1, 0))
    return comp


def daily_trend(daily_close: pd.Series) -> pd.Series:
    """روند روزانه صعودی: close > EMA50 و EMA20 > EMA50"""
    e20 = daily_close.ewm(span=20, adjust=False).mean()
    e50 = daily_close.ewm(span=50, adjust=False).mean()
    ok = (daily_close > e50) & (e20 > e50)
    ok.iloc[:50] = False                       # دوره گرم‌شدن
    return ok


def build_signal(symbol: str, p: Params | None = None) -> dict:
    p = p or Params()
    df = add_indicators(market.fetch_ohlcv(symbol, "1h", 40))
    if len(df) < 250:
        raise ValueError(f"داده کافی برای {symbol} وجود ندارد (ارز جدید است؟).")

    comp = compute_components(df)
    score = comp.sum(axis=1)
    row, tech = df.iloc[-2], float(score.iloc[-2])       # آخرین کندل بسته‌شده
    news = fetch_news(symbol)
    adj = news_adjustment(news)
    total = tech + adj

    dfd = market.fetch_ohlcv(symbol, "1d", 150, ttl=600)
    daily_ok = bool(len(dfd) >= 55 and daily_trend(dfd["close"]).iloc[-2])

    price, atr = float(df["close"].iloc[-1]), float(row.atr)
    risk = atr * p.atr_mult
    blocked = False
    if total >= p.buy_threshold:
        action = "BUY"
        if p.daily_filter and not daily_ok:
            action, blocked = "HOLD", True
    elif total <= p.sell_threshold:
        action = "SELL"
    else:
        action = "HOLD"

    levels = rebuy = None
    if action == "BUY":
        levels = {
            "entry": price, "stop_loss": price - risk, "stop_pct": -risk / price * 100,
            "targets": [{"r": r, "price": price + risk * r, "pct": risk * r / price * 100} for r in TARGETS_R],
        }
    elif action == "SELL":
        rebuy = [{"label": f"ناحیه بازخرید احتمالی {i}", "price": price - risk * i,
                  "pct": -risk * i / price * 100} for i in (1, 2)]

    reasons = [{"label": LABELS[k], "points": int(comp[k].iloc[-2])} for k in comp.columns]
    reasons.append({"label": "اخبار (سنتیمنت ارز و بازار)", "points": adj})

    tail = df.iloc[-CHART_CANDLES:]
    t = to_epoch(tail.index)
    candles = np.column_stack([t, tail[["open", "high", "low", "close", "volume"]].to_numpy()])
    return {
        "symbol": symbol, "action": action, "blocked": blocked, "price": price,
        "candle_time": int(to_epoch(df.index[[-2]])[0]),
        "score": {"tech": tech, "news": adj, "total": total},
        "buy_threshold": p.buy_threshold, "sell_threshold": p.sell_threshold,
        "rsi": float(row.rsi), "atr": atr, "atr_pct": atr / price * 100,
        "ema200_up": bool(row.close > row.ema200), "daily_up": daily_ok,
        "levels": levels, "rebuy": rebuy, "risk_pct": risk / price * 100, "reasons": reasons,
        "chart": {
            "candles": candles,
            "ema20": tail["ema20"].to_numpy(), "ema50": tail["ema50"].to_numpy(),
            "ema200": tail["ema200"].to_numpy(),
        },
        "news": news,
    }
