import math

import numpy as np
import pandas as pd


def to_epoch(idx: pd.DatetimeIndex) -> np.ndarray:
    """زمان UTC (naive) → ثانیه یونیکس، مستقل از واحد زمانی pandas"""
    return ((idx - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy().astype("int64")


def clean(o):
    """تبدیل ساختارهای numpy/pandas به JSON امن (NaN → null)"""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o
