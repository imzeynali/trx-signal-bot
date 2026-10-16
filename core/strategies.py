"""استراتژی‌های اسپات (فقط خرید/خروج). هر استراتژی دو آرایه bool برمی‌گرداند: (entry, exit)"""
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

BUY_TH, SELL_TH = 4, -3


def _up(a: pd.Series, b: pd.Series) -> np.ndarray:
    return ((a > b) & (a.shift() <= b.shift())).to_numpy(dtype=bool)


def _down(a: pd.Series, b: pd.Series) -> np.ndarray:
    return ((a < b) & (a.shift() >= b.shift())).to_numpy(dtype=bool)


def compute_components(d: pd.DataFrame) -> pd.DataFrame:
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


def daily_trend_allow(d: pd.DataFrame) -> np.ndarray:
    """روند روزانه صعودی (روز بسته‌شده قبلی): close > EMA50 و EMA20 > EMA50"""
    dc = d["close"].resample("1D").last().dropna()
    e20, e50 = dc.ewm(span=20, adjust=False).mean(), dc.ewm(span=50, adjust=False).mean()
    ok = (dc > e50) & (e20 > e50)
    ok.iloc[:50] = False
    ok = ok.shift(1, fill_value=False)
    return ok.reindex(d.index.floor("D"), fill_value=False).to_numpy(dtype=bool)


def s_multi(d):
    score = compute_components(d).sum(axis=1)
    entry = (score >= BUY_TH).to_numpy(dtype=bool) & daily_trend_allow(d)
    return entry, (score <= SELL_TH).to_numpy(dtype=bool)


def s_ema_cross(d):
    return _up(d.ema20, d.ema50) & (d.close > d.ema200).to_numpy(), _down(d.ema20, d.ema50)


def s_rsi(d):
    entry = ((d.rsi.shift() < 30) & (d.rsi >= 30) & (d.ema50 > d.ema200)).to_numpy(dtype=bool)
    return entry, (d.rsi >= 60).to_numpy(dtype=bool)


def s_macd(d):
    return _up(d.macd, d.macd_sig) & (d.close > d.ema200).to_numpy(), _down(d.macd, d.macd_sig)


def s_boll(d):
    entry = ((d.close.shift() < d.bb_lo.shift()) & (d.close >= d.bb_lo) & (d.ema50 > d.ema200)).to_numpy(dtype=bool)
    return entry, (d.close >= d.bb_mid).to_numpy(dtype=bool)


def s_breakout(d):
    entry = ((d.close > d.hh20) & (d.close > d.ema200) & (d.volume > d.vol_ma)).to_numpy(dtype=bool)
    return entry, (d.close < d.ll10).to_numpy(dtype=bool)


@dataclass(frozen=True)
class Strategy:
    key: str
    short: str
    name: str
    desc: str
    fn: Callable


STRATEGIES = {s.key: s for s in [
    Strategy("multi", "امتیازی چندشاخصه", "امتیازی چندشاخصه",
             "ترکیب EMA، RSI، MACD، بولینگر و حجم + فیلتر روند روزانه صعودی", s_multi),
    Strategy("ema_cross", "کراس EMA 20/50", "کراس EMA 20/50",
             "خرید با کراس صعودی EMA20 از EMA50 بالای EMA200؛ خروج با کراس نزولی", s_ema_cross),
    Strategy("macd_cross", "کراس MACD", "کراس MACD",
             "خرید با کراس صعودی MACD از خط سیگنال بالای EMA200؛ خروج با کراس نزولی", s_macd),
    Strategy("rsi_rev", "بازگشت RSI", "بازگشت RSI از اشباع فروش",
             "خرید پس از خروج RSI از زیر ۳۰ در ساختار صعودی؛ خروج با RSI بالای ۶۰", s_rsi),
    Strategy("boll_bounce", "بازگشت بولینگر", "بازگشت از باند پایین بولینگر",
             "خرید پس از بازگشت قیمت به بالای باند پایین؛ خروج در میانه باند", s_boll),
    Strategy("breakout", "شکست کانال", "شکست کانال دانچین (روندی)",
             "خرید با شکست سقف ۲۰ کندل قبل همراه حجم بالا؛ خروج با شکست کف ۱۰ کندل", s_breakout),
]}
ORDER = list(STRATEGIES)
