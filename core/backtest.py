from dataclasses import asdict

import numpy as np
import pandas as pd

from .strategy import WARMUP_DAYS, Params, add_indicators, compute_components, daily_trend
from .util import to_epoch


def run_backtest(df: pd.DataFrame, p: Params) -> dict:
    """df شامل دوره گرم‌شدن (WARMUP_DAYS) در ابتدای خود است؛ معامله فقط بعد از آن باز می‌شود."""
    d = add_indicators(df)
    d["score"] = compute_components(d).sum(axis=1)
    d = d.dropna()
    idx = d.index
    start = df.index[0] + pd.Timedelta(days=WARMUP_DAYS)
    active = np.asarray(idx >= start)

    if p.daily_filter:
        dc = df["close"].resample("1D").last().dropna()
        trend = daily_trend(dc).shift(1).fillna(False)      # فقط روز بسته‌شده قبلی (بدون نگاه به آینده)
        allow = trend.reindex(idx.floor("D")).fillna(False).to_numpy(dtype=bool)
    else:
        allow = np.ones(len(d), dtype=bool)

    score, close, high, low, atr = (d[c].to_numpy() for c in ("score", "close", "high", "low", "atr"))
    t = to_epoch(idx)
    pos, trades = None, []
    for i in range(len(d)):
        if not active[i]:
            continue
        if pos:
            ex_price = reason = None
            if low[i] <= pos["sl"]:                         # محافظه‌کارانه: اول حد ضرر
                ex_price, reason = pos["sl"], "SL"
            elif high[i] >= pos["tp"]:
                ex_price, reason = pos["tp"], "TP"
            elif score[i] <= p.sell_threshold:
                ex_price, reason = close[i], "SIGNAL"
            elif i - pos["i"] >= p.max_hold:
                ex_price, reason = close[i], "TIME"
            if ex_price is not None:
                ret = ex_price * (1 - p.fee) / (pos["entry"] * (1 + p.fee)) - 1
                trades.append({"entry_time": int(t[pos["i"]]), "entry_price": float(pos["entry"]),
                               "exit_time": int(t[i]), "exit_price": float(ex_price),
                               "reason": reason, "ret": float(ret)})
                pos = None
        elif score[i] >= p.buy_threshold and allow[i]:
            risk = atr[i] * p.atr_mult
            pos = {"i": i, "entry": close[i], "sl": close[i] - risk, "tp": close[i] + risk * p.tp_r}

    sel = df[df.index >= start]
    stats = summarize(trades, start, df.index[-1], float(sel["close"].iloc[0]), float(sel["close"].iloc[-1]))
    stats["open_trade"] = pos is not None

    equity, eq = [[int(to_epoch(sel.index[[0]])[0]), 1.0]], 1.0
    for tr in trades:
        eq *= 1 + tr["ret"]
        equity.append([tr["exit_time"], eq])

    st = to_epoch(sel.index)
    candles = np.column_stack([st, sel[["open", "high", "low", "close"]].to_numpy()])
    return {"params": asdict(p), "stats": stats, "trades": trades, "equity": equity,
            "candles": candles, "start": int(st[0]), "end": int(st[-1])}


def summarize(trades: list, start, end, price_start: float, price_end: float) -> dict:
    days = max((end - start).days, 1)
    out = {"days": days, "buy_hold": (price_end / price_start - 1) * 100, "trades": len(trades)}
    if not trades:
        return {**out, "empty": True}

    ret = pd.Series([x["ret"] for x in trades],
                    index=pd.to_datetime([x["exit_time"] for x in trades], unit="s"))
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
                "avg_active": float(g.mean() * 100),             # میانگین فقط دوره‌های دارای معامله
                "loss_rate": float((g < 0).mean() * 100),        # نرخ خطا: درصد دوره‌های زیان‌ده
                "active": int(len(g))}

    monthly = periods("M")
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
        "monthly_returns": [{"period": str(k), "ret": float(v * 100)} for k, v in monthly.items()],
    }
