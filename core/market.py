import threading
import time

import ccxt
import pandas as pd

from .config import EXCHANGE

_ex = None
_ex_lock = threading.Lock()
_fetch_lock = threading.Lock()
_cache: dict = {}
_cache_lock = threading.Lock()
_symbols_cache: dict = {}


def get_exchange():
    global _ex
    with _ex_lock:
        if _ex is None:
            ex = getattr(ccxt, EXCHANGE)({"enableRateLimit": True, "timeout": 20000})
            ex.load_markets()
            _ex = ex
        return _ex


def list_symbols() -> list[str]:
    """همه جفت‌ارزهای اسپات USDT صرافی"""
    if _symbols_cache.get("t", 0) > time.time() - 3600:
        return _symbols_cache["v"]
    ex = get_exchange()
    syms = sorted(s for s, m in ex.markets.items()
                  if m.get("spot") and m.get("quote") == "USDT" and m.get("active") is not False)
    _symbols_cache.update(t=time.time(), v=syms)
    return syms


def resolve_symbol(text: str) -> str:
    s = (text or "").upper().replace("-", "/").replace(" ", "").strip()
    if not s:
        raise ValueError("نماد ارز را وارد کنید.")
    if "/" not in s:
        s += "/USDT"
    m = get_exchange().markets.get(s)
    if not m or not m.get("spot"):
        raise ValueError(f"نماد {s} در صرافی {EXCHANGE} پیدا نشد (فقط جفت‌ارزهای اسپات).")
    return s


def fetch_ohlcv(symbol: str, timeframe: str = "1h", days: int = 30, ttl: int = 120) -> pd.DataFrame:
    key = (symbol, timeframe, days)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1].copy()

    ex = get_exchange()
    step = ex.parse_timeframe(timeframe) * 1000
    since = ex.milliseconds() - days * 86400 * 1000
    rows: list = []
    with _fetch_lock:
        for _ in range(200):                          # سقف ایمنی
            batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not batch:
                break
            rows += batch
            nxt = batch[-1][0] + step
            if nxt <= since or nxt >= ex.milliseconds():
                break                                  # به انتها رسیدیم (طول batch در صرافی‌ها فرق دارد)
            since = nxt
    if not rows:
        raise ValueError(f"داده‌ای برای {symbol} دریافت نشد.")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df["ts"], unit="ms")
    df = df.drop(columns="ts").astype(float)

    with _cache_lock:
        if len(_cache) > 60:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
        _cache[key] = (now, df)
    return df.copy()
