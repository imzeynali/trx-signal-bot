"""موتور مدیریت پوزیشن اسپات — مشترک بین بک‌تست و معامله شبیه‌سازی‌شده

حد ضرر اولیه 1.5×ATR؛ ۳ تارگت (1R/2R/3R) هر کدام یک‌سوم حجم؛
با رسیدن به تارگت اول، حد ضرر به نقطه ورود می‌رود. اگر در یک کندل هم SL و هم TP لمس شود، SL فرض می‌شود.
"""
from dataclasses import dataclass, field

from .config import FRACS


@dataclass
class Position:
    entry: float
    sl: float
    targets: list
    hit: list = field(default_factory=lambda: [False, False, False])
    remaining: float = 1.0
    realized: float = 0.0       # مجموع بازده خالص (کسری از کل پوزیشن)
    closed: bool = False
    reason: str = ""


def net_return(entry: float, exit_: float, fee: float) -> float:
    return exit_ * (1 - fee) / (entry * (1 + fee)) - 1


def _close_part(pos: Position, price: float, frac: float, fee: float):
    frac = min(frac, pos.remaining)
    pos.realized += frac * net_return(pos.entry, price, fee)
    pos.remaining -= frac


def advance(pos: Position, high: float, low: float, close: float, fee: float,
            exit_signal: bool = False, time_stop: bool = False) -> list:
    """یک کندل را روی پوزیشن اعمال می‌کند؛ لیست رویدادها را برمی‌گرداند"""
    ev: list = []

    def stop_hit() -> bool:
        if low <= pos.sl:
            reason = "BE" if pos.sl >= pos.entry else "SL"
            _close_part(pos, pos.sl, pos.remaining, fee)
            pos.closed, pos.reason = True, reason
            ev.append({"type": reason, "price": pos.sl})
            return True
        return False

    if stop_hit():
        return ev
    for i, t in enumerate(pos.targets):
        if pos.hit[i]:
            continue
        if high < t:
            break
        _close_part(pos, t, FRACS[i] if i < 2 else pos.remaining, fee)
        pos.hit[i] = True
        ev.append({"type": f"TP{i + 1}", "price": t})
        if i == 0:
            pos.sl = max(pos.sl, pos.entry)
        if pos.remaining <= 1e-9:
            pos.closed, pos.reason = True, "TP3"
            return ev
        if stop_hit():                  # محافظه‌کارانه: سر‌به‌سر در همان کندل
            return ev
    if exit_signal or time_stop:
        _close_part(pos, close, pos.remaining, fee)
        pos.closed, pos.reason = True, "SIGNAL" if exit_signal else "TIME"
        ev.append({"type": pos.reason, "price": close})
    return ev
