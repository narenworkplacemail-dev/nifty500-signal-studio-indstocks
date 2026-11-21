#!/usr/bin/env python3
"""Daily NIFTY 500 technical scan using INDstocks historical candles."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from indstocks_data import fetch_daily_frames, load_universe


def wilder_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    close, high, low = df["Close"].astype(float), df["High"].astype(float), df["Low"].astype(float)
    df["EMA20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    df["EMA50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    delta = close.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain, avg_loss = wilder_ema(gain, 14), wilder_ema(loss, 14)
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI14"] = 100 - (100 / (1 + rs))
    df.loc[(avg_loss == 0) & (avg_gain > 0), "RSI14"] = 100.0
    df.loc[(avg_loss == 0) & (avg_gain == 0), "RSI14"] = 50.0
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    up, down = high.diff(), -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = wilder_ema(tr, 14)
    df["DI+14"] = 100 * wilder_ema(plus_dm, 14) / atr.replace(0, float("nan"))
    df["DI-14"] = 100 * wilder_ema(minus_dm, 14) / atr.replace(0, float("nan"))
    di_sum = df["DI+14"] + df["DI-14"]
    dx = 100 * (df["DI+14"] - df["DI-14"]).abs() / di_sum.replace(0, float("nan"))
    df["ADX14"] = wilder_ema(dx, 14)
    return df


def latest_signal(frame: pd.DataFrame, name: str, symbol: str, require_breakout: bool) -> tuple[dict | None, str]:
    df = add_indicators(frame).dropna(subset=["Close", "EMA20", "EMA50", "RSI14", "ADX14", "DI+14", "DI-14"])
    if len(df) < 6:
        return None, "insufficient_valid_history"
    row = df.iloc[-1]
    if not (row["ADX14"] > 25 and row["DI+14"] > row["DI-14"]):
        return None, "adx_or_di_filter"
    if not (row["Close"] > row["EMA20"] > row["EMA50"]):
        return None, "ema_stack_filter"
    if not (50 <= row["RSI14"] <= 75):
        return None, "rsi_filter"
    crossed = (df["EMA20"].shift(1) <= df["EMA50"].shift(1)) & (df["EMA20"] > df["EMA50"])
    dates = df.index[crossed.fillna(False)]
    cross_date = pd.Timestamp(dates[-1]).strftime("%Y-%m-%d") if len(dates) else ""
    prev5 = df["Close"].iloc[-6:-1]
    breakout = bool(row["Close"] > prev5.max())
    if require_breakout and not breakout:
        return None, "five_session_breakout_filter"
    stamp = pd.Timestamp(df.index[-1])
    record = {
        "stock_name": name, "symbol": symbol, "close_price": round(float(row["Close"]), 2),
        "data_date": stamp.strftime("%Y-%m-%d"), "ema20": round(float(row["EMA20"]), 2),
        "ema50": round(float(row["EMA50"]), 2), "ema20_crossed_above_ema50_date": cross_date,
        "five_session_close_breakout": "YES" if breakout else "NO",
        "previous_5_session_closes_oldest_to_newest": ", ".join(f"{float(x):.2f}" for x in prev5),
        "rsi14": round(float(row["RSI14"]), 2), "adx14": round(float(row["ADX14"]), 2),
        "di_plus14": round(float(row["DI+14"]), 2), "di_minus14": round(float(row["DI-14"]), 2),
        "breakout_required": "YES" if require_breakout else "NO",
    }
    return record, "passed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("nifty500_scan_indstocks.csv"))
    parser.add_argument("--universe-csv", type=Path)
    parser.add_argument("--require-5day-breakout", action="store_true")
    parser.add_argument("--history-days", type=int, default=365)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("INDSTOCKS_ACCESS_TOKEN", "").strip()
    if not token:
        logging.error("Set INDSTOCKS_ACCESS_TOKEN to your INDstocks access token first.")
        return 2
    if not 60 <= args.history_days <= 3650:
        logging.error("--history-days must be between 60 and 3650")
        return 2
    try:
        universe, _ = load_universe(token, str(args.universe_csv) if args.universe_csv else None)
        logging.info("Loaded %d NSE constituents; requesting %d days of daily candles", len(universe), args.history_days)
        codes = universe["scrip_code"].tolist()
        frames, failed = fetch_daily_frames(token, codes, args.history_days)
    except Exception as exc:
        logging.error("INDstocks data could not be loaded: %s", exc)
        return 2
    records, issues = [], []
    for item in universe.itertuples(index=False):
        frame = frames.get(item.scrip_code)
        if frame is None or frame.empty:
            issues.append({"symbol": item.symbol, "issue": "no_usable_indstocks_daily_data"})
            continue
        try:
            record, reason = latest_signal(frame, item.stock_name, item.symbol, args.require_5day_breakout)
            if record:
                records.append(record)
            elif reason == "insufficient_valid_history":
                issues.append({"symbol": item.symbol, "issue": reason})
        except Exception as exc:
            issues.append({"symbol": item.symbol, "issue": f"calculation_error: {exc}"})
    for code in failed:
        match = universe.loc[universe.scrip_code == code, "symbol"]
        if not match.empty:
            issues.append({"symbol": match.iloc[0], "issue": "empty_response_from_indstocks"})
    output = pd.DataFrame(records)
    if not output.empty:
        output.sort_values(["adx14", "rsi14"], ascending=[False, True], inplace=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False)
    if issues:
        pd.DataFrame(issues).drop_duplicates().to_csv(args.output.with_name(f"{args.output.stem}_data_issues.csv"), index=False)
    print(f"Scan completed at {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}")
    print(f"Matches: {len(output)} | Data issues: {len(issues)} | Report: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
