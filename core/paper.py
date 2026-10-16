"""معامله شبیه‌سازی‌شده (Paper Trading) با قیمت‌های واقعی؛ هیچ پولی جابه‌جا نمی‌شود."""
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing

import pandas as pd

from . import market
from .config import DB_PATH, FEE, MAX_OPEN_TRADES, PAPER_TRADE_SIZE, TF
from .engine import Position, _close_part, advance, net_return
from .indicators import add_indicators
from .strategies import STRATEGIES

log = logging.getLogger("paper")
_lock = threading.Lock()

SCHEMA = """CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER,
  symbol TEXT, strategy TEXT, tf TEXT, status TEXT DEFAULT 'open',
  entry_price REAL, entry_time INTEGER, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
  hit1 INTEGER DEFAULT 0, hit2 INTEGER DEFAULT 0, hit3 INTEGER DEFAULT 0,
  remaining REAL DEFAULT 1, realized REAL DEFAULT 0, size REAL,
  exit_time INTEGER, close_reason TEXT, forced INTEGER DEFAULT 0,
  last_check_ms INTEGER, last_sig_ms INTEGER)"""


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with _lock, closing(_conn()) as c:
        c.execute(SCHEMA)
        c.commit()


def _q(sql: str, args=()) -> list:
    with _lock, closing(_conn()) as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _exec(sql: str, args=()) -> int:
    with _lock, closing(_conn()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def update_trade(tid: int, **f):
    cols = ", ".join(f"{k}=?" for k in f)
    _exec(f"UPDATE trades SET {cols} WHERE id=?", (*f.values(), tid))


def get_trade(tid: int) -> dict | None:
    r = _q("SELECT * FROM trades WHERE id=?", (tid,))
    return r[0] if r else None


def open_trades(user_id: int | None = None) -> list:
    if user_id is None:
        return _q("SELECT * FROM trades WHERE status='open'")
    return _q("SELECT * FROM trades WHERE status='open' AND user_id=? ORDER BY id", (user_id,))


def open_from_suggestion(user_id: int, chat_id: int, s: dict, forced: bool) -> int:
    mine = open_trades(user_id)
    if len(mine) >= MAX_OPEN_TRADES:
        raise ValueError(f"حداکثر {MAX_OPEN_TRADES} معامله باز همزمان مجاز است؛ ابتدا یکی را ببندید.")
    if any(t["symbol"] == s["symbol"] and t["strategy"] == s["key"] and t["tf"] == s["tf"] for t in mine):
        raise ValueError("یک معامله باز با همین ارز، استراتژی و تایم‌فریم دارید.")
    L, now = s["levels"], int(time.time())
    return _exec(
        """INSERT INTO trades(user_id,chat_id,symbol,strategy,tf,entry_price,entry_time,sl,tp1,tp2,tp3,size,forced,
           last_check_ms,last_sig_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, chat_id, s["symbol"], s["key"], s["tf"], L["entry"], now, L["stop_loss"],
         *[t["price"] for t in L["targets"]], PAPER_TRADE_SIZE, int(forced),
         (now // 60 + 1) * 60 * 1000, now * 1000))


# ───────────────────────── پایش ─────────────────────────
def _pos(t: dict) -> Position:
    return Position(entry=t["entry_price"], sl=t["sl"], targets=[t["tp1"], t["tp2"], t["tp3"]],
                    hit=[bool(t["hit1"]), bool(t["hit2"]), bool(t["hit3"])],
                    remaining=t["remaining"], realized=t["realized"])


def _save(t: dict, pos: Position, **extra):
    f = dict(sl=pos.sl, hit1=int(pos.hit[0]), hit2=int(pos.hit[1]), hit3=int(pos.hit[2]),
             remaining=max(pos.remaining, 0.0), realized=pos.realized, **extra)
    if pos.closed:
        f.update(status="closed", exit_time=int(time.time()), close_reason=pos.reason)
    update_trade(t["id"], **f)


EVENT_TEXT = {"SL": "🛑 <b>حد ضرر فعال شد</b>", "BE": "⚖️ <b>خروج در نقطه سر‌به‌سر</b> (بعد از تارگت ۱)",
              "SIGNAL": "📉 <b>سیگنال خروج استراتژی</b>", "TIME": "⏰ <b>پایان زمان نگهداری</b>",
              "MANUAL": "✋ <b>بستن دستی</b>", "TP1": "🎯 <b>تارگت ۱ رسید</b> — ۱/۳ حجم فروخته شد؛ حد ضرر به نقطه ورود رفت",
              "TP2": "🎯 <b>تارگت ۲ رسید</b> — ۱/۳ حجم فروخته شد",
              "TP3": "🏆 <b>تارگت ۳ رسید</b> — بقیه حجم فروخته شد"}


def _fp(x: float) -> str:
    return f"{x:.6f}" if x < 0.01 else f"{x:.5f}" if x < 1 else f"{x:.4f}" if x < 100 else f"{x:.2f}"


def _message(t: dict, events: list, pos: Position) -> str:
    lines = [f"{EVENT_TEXT[e['type']]}\n   {t['symbol']} (#{t['id']}) — قیمت: <code>{_fp(e['price'])}</code>" for e in events]
    if pos.closed:
        lines.append(f"\n✅ معامله بسته شد | نتیجه نهایی: <b>{pos.realized * 100:+.2f}%</b> "
                     f"(≈ {pos.realized * t['size']:+.2f} USDT)")
    else:
        lines.append(f"\n📌 باقی‌مانده پوزیشن: {pos.remaining * 100:.0f}% | حد ضرر فعلی: <code>{_fp(pos.sl)}</code>")
    return "\n".join(lines)


def check_trade(t: dict, now_ms: int | None = None) -> str | None:
    """پایش یک معامله باز: SL/TP با کندل 1 دقیقه‌ای + سیگنال خروج استراتژی روی کندل بسته‌شده"""
    now_ms = now_ms or int(time.time() * 1000)
    pos, events = _pos(t), []
    since = t["last_check_ms"] or ((t["entry_time"] // 60 + 1) * 60 * 1000)
    last_ts, last_close = since, None
    for _ in range(10):
        rows = market.fetch_recent(t["symbol"], "1m", since, 1000)
        if not rows:
            break
        for ts, o, h, l, c, v in rows:
            if ts < since:
                continue
            events += advance(pos, h, l, c, FEE)
            last_ts, last_close = ts, c
            if pos.closed:
                break
        if pos.closed or len(rows) < 1000:
            break
        since = rows[-1][0] + 60_000

    last_sig = t["last_sig_ms"] or 0
    if not pos.closed:
        tf_ms = TF[t["tf"]]["ms"]
        candle_open = (now_ms // tf_ms) * tf_ms - tf_ms            # شروع آخرین کندل بسته‌شده
        if candle_open > last_sig and candle_open >= (t["entry_time"] * 1000 // tf_ms) * tf_ms:
            df = market.fetch_ohlcv(t["symbol"], t["tf"], TF[t["tf"]]["live_days"], ttl=20)
            d = add_indicators(df)
            _, exit_ = STRATEGIES[t["strategy"]].fn(d)
            ts = pd.Timestamp(candle_open, unit="ms")
            if ts in d.index and exit_[d.index.get_loc(ts)]:
                price = last_close if last_close is not None else market.last_price(t["symbol"])
                _close_part(pos, price, pos.remaining, FEE)
                pos.closed, pos.reason = True, "SIGNAL"
                events.append({"type": "SIGNAL", "price": price})
            last_sig = candle_open

    _save(t, pos, last_check_ms=last_ts, last_sig_ms=last_sig)
    return _message(t, events, pos) if events else None


def check_all() -> list:
    out = []
    for t in open_trades():
        try:
            msg = check_trade(t)
            if msg:
                out.append((t["chat_id"], msg))
        except Exception:
            log.exception("check trade %s failed", t["id"])
    return out


def close_manual(tid: int, user_id: int) -> str:
    t = get_trade(tid)
    if not t or t["user_id"] != user_id or t["status"] != "open":
        raise ValueError("معامله پیدا نشد یا قبلاً بسته شده است.")
    pos = _pos(t)
    price = market.last_price(t["symbol"])
    _close_part(pos, price, pos.remaining, FEE)
    pos.closed, pos.reason = True, "MANUAL"
    _save(t, pos)
    return _message(t, [{"type": "MANUAL", "price": price}], pos)


# ───────────────────────── گزارش ─────────────────────────
def report(user_id: int) -> dict:
    rows = _q("SELECT * FROM trades WHERE user_id=? ORDER BY id", (user_id,))
    closed = [t for t in rows if t["status"] == "closed"]
    opened = [t for t in rows if t["status"] == "open"]
    now = time.time()
    day0 = now - now % 86400
    periods = {}
    for name, since in (("today", day0), ("d7", now - 7 * 86400), ("d30", now - 30 * 86400),
                        ("d365", now - 365 * 86400), ("all", 0)):
        sel = [t for t in closed if (t["exit_time"] or 0) >= since]
        periods[name] = {"n": len(sel), "usdt": sum(t["realized"] * t["size"] for t in sel),
                         "pct": sum(t["realized"] for t in sel) * 100}
    wins = [t["realized"] for t in closed if t["realized"] > 0]
    losses = [t["realized"] for t in closed if t["realized"] <= 0]
    open_info = []
    for t in opened:
        try:
            price = market.last_price(t["symbol"])
        except Exception:
            price = None
        unreal = t["remaining"] * net_return(t["entry_price"], price, FEE) if price else 0.0
        open_info.append({**t, "price": price, "unreal": unreal, "total": t["realized"] + unreal})
    return {"size": PAPER_TRADE_SIZE, "periods": periods, "closed_n": len(closed), "wins": len(wins),
            "losses": len(losses), "avg_win": sum(wins) / len(wins) * 100 if wins else 0.0,
            "avg_loss": sum(losses) / len(losses) * 100 if losses else 0.0,
            "open": open_info, "last_closed": list(reversed(closed))[:5]}
