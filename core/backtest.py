from collections import Counter

import numpy as np
import pandas as pd

from .config import ATR_MULT, FEE, TARGETS_R, TF, WARMUP_DAYS
from .engine import Position, advance
from .indicators import add_indicators
from .strategies import STRATEGIES
from .util import to_epoch


def run_backtest(df: pd.DataFrame, key: str, tf: str) -> dict:
    """df شامل WARMUP_DAYS روز اضافه در ابتدا است؛ معامله فقط بعد از آن باز می‌شود."""
    cfg, strat = TF[tf], STRATEGIES[key]
    d = add_indicators(df)
    entry, exit_ = strat.fn(d)
    start = df.index[0] + pd.Timedelta(days=WARMUP_DAYS)
    active = np.asarray(d.index >= start)
    valid = d[["atr", "rsi", "bb_up", "vol_ma", "hh20", "ll10"]].notna().all(axis=1).to_numpy()
    high, low, close, atr = (d[c].to_numpy() for c in ("high", "low", "close", "atr"))
    t = to_epoch(d.index)

    pos, pos_i, trades = None, 0, []
    for i in range(len(d)):
        if not active[i]:
            continue
        if pos:
            advance(pos, high[i], low[i], close[i], FEE, bool(exit_[i]), i - pos_i >= cfg["max_hold"])
            if pos.closed:
                trades.append({"entry_time": int(t[pos_i]), "exit_time": int(t[i]), "ret": float(pos.realized),
                               "reason": pos.reason, "tp_hits": int(sum(pos.hit))})
                pos = None
        elif entry[i] and valid[i]:
            risk = atr[i] * ATR_MULT
            pos_i = i
            pos = Position(entry=close[i], sl=close[i] - risk, targets=[close[i] + risk * r for r in TARGETS_R])

    sel = df[df.index >= start]
    stats = summarize(trades, start, df.index[-1], float(sel["close"].iloc[0]), float(sel["close"].iloc[-1]))
    stats["open_trade"] = pos is not None
    stats["reasons"] = dict(Counter(x["reason"] for x in trades))
    stats["tp1_rate"] = float(np.mean([x["tp_hits"] >= 1 for x in trades]) * 100) if trades else 0.0
    return {"strategy": key, "tf": tf, "stats": stats, "start": int(to_epoch(sel.index[[0]])[0]),
            "end": int(to_epoch(sel.index[[-1]])[0])}


def summarize(trades: list, start, end, price_start: float, price_end: float) -> dict:
    days = max((end - start).days, 1)
    out = {"days": days, "buy_hold": (price_end / price_start - 1) * 100, "trades": len(trades)}
    if not trades:
        return {**out, "empty": True}

    ret = pd.Series([x["ret"] for x in trades], index=pd.to_datetime([x["exit_time"] for x in trades], unit="s"))
    eq = (1 + ret).cumprod()
    total = float(eq.iloc[-1])
    months, years = days / 30.44, days / 365.0
    wins, losses = ret[ret > 0], ret[ret <= 0]

    streak = best = 0
    for r in ret:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)

    def periods(freq):
        return ret.groupby(ret.index.to_period(freq)).agg(lambda x: (1 + x).prod() - 1)

    def block(freq, n):
        g = periods(freq)
        return {"avg": (total ** (1 / n) - 1) * 100,            # میانگین هندسی سود هر دوره تقویمی
                "loss_rate": float((g < 0).mean() * 100),        # نرخ خطا: درصد دوره‌های زیان‌ده
                "active": int(len(g))}

    return {
        **out,
        "win_rate": float((ret > 0).mean() * 100), "loss_rate": float((ret <= 0).mean() * 100),
        "avg_win": float(wins.mean() * 100) if len(wins) else 0.0,
        "avg_loss": float(losses.mean() * 100) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None,
        "total_return": (total - 1) * 100,
        "max_dd": float((eq / eq.cummax() - 1).min() * 100),
        "max_loss_streak": int(best),
        "daily": block("D", days), "monthly": block("M", months), "yearly": block("Y", years),
        "monthly_returns": [{"period": str(k), "ret": float(v * 100)} for k, v in periods("M").items()],
    }
