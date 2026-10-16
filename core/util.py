import numpy as np
import pandas as pd


def to_epoch(idx: pd.DatetimeIndex) -> np.ndarray:
    """زمان UTC (naive) → ثانیه یونیکس"""
    return ((idx - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy().astype("int64")
