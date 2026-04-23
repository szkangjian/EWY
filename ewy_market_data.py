"""
Utilities for loading EWY minute bars on a consistent US/Eastern trading-day basis.

Historical Polygon exports in this repo were written as naive UTC timestamps,
while Yahoo updates are saved as naive US/Eastern timestamps. Strategy research
should use one clock and a consistent regular-session definition.
"""

from __future__ import annotations

import pandas as pd

MARKET_TZ = "US/Eastern"
UTC_TZ = "UTC"
REGULAR_OPEN_MINUTE = 9 * 60 + 30
REGULAR_CLOSE_MINUTE = 16 * 60


def infer_naive_timestamp_tz(values: pd.Series | pd.DatetimeIndex) -> str:
    """
    Infer whether naive timestamps are UTC-like or already market-local.

    Regular US/Eastern equity bars should usually fall between roughly 04:00 and
    20:00. If late-evening / overnight hours are present, the data is almost
    certainly naive UTC that still needs conversion.
    """

    ts = pd.DatetimeIndex(pd.to_datetime(values))
    hours = pd.Series(ts.hour)
    if hours.isin([0, 1, 2, 3, 21, 22, 23]).any():
        return UTC_TZ
    return MARKET_TZ


def normalize_timestamp_series(
    values: pd.Series,
    source_tz: str | None = None,
    target_tz: str = MARKET_TZ,
) -> pd.Series:
    """Normalize timestamps to naive US/Eastern for trading-day calculations."""
    ts = pd.to_datetime(values)
    if ts.dt.tz is not None:
        return ts.dt.tz_convert(target_tz).dt.tz_localize(None)

    source_tz = source_tz or infer_naive_timestamp_tz(ts)
    if source_tz == target_tz:
        return ts

    return ts.dt.tz_localize(source_tz).dt.tz_convert(target_tz).dt.tz_localize(None)


def load_minute_data(csv_path: str, source_tz: str | None = None) -> pd.DataFrame:
    """Load minute data and normalize timestamps to naive US/Eastern."""
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df["timestamp"] = normalize_timestamp_series(df["timestamp"], source_tz=source_tz)
    return df.sort_values("timestamp").reset_index(drop=True)


def add_trade_date(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = out["timestamp"].dt.date
    return out


def filter_regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only regular-session bars: 09:30 <= t < 16:00 ET."""
    minutes = df["timestamp"].dt.hour * 60 + df["timestamp"].dt.minute
    mask = (minutes >= REGULAR_OPEN_MINUTE) & (minutes < REGULAR_CLOSE_MINUTE)
    return df.loc[mask].copy()


def load_regular_session_data(csv_path: str, source_tz: str | None = None) -> pd.DataFrame:
    """Load minute data normalized to market time and filtered to the regular session."""
    return add_trade_date(filter_regular_session(load_minute_data(csv_path, source_tz=source_tz)))


def build_daily_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate regular-session minute bars into daily OHLCV."""
    if "date" not in df.columns:
        df = add_trade_date(df)

    daily = (
        df.groupby("date")
        .agg(
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
            Vol=("Volume", "sum"),
        )
        .reset_index()
    )
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["date"] = pd.to_datetime(daily["date"])
    return daily


def load_daily_bars(csv_path: str, source_tz: str | None = None) -> pd.DataFrame:
    """Convenience wrapper for ET-normalized regular-session daily bars."""
    return build_daily_bars(load_regular_session_data(csv_path, source_tz=source_tz))
