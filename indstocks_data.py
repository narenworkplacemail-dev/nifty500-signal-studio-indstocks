"""INDstocks market-data adapter for the NIFTY 500 Signal Studio."""
from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE_URL = "https://api.indstocks.com"
NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
IST = ZoneInfo("Asia/Kolkata")


def headers(token: str) -> dict[str, str]:
    token = token.strip()
    if not token:
        raise ValueError("Enter your INDstocks access token in the sidebar.")
    return {"Authorization": token}


def load_universe(token: str, local_csv: str | None = None) -> tuple[pd.DataFrame, str]:
    """Load NSE constituents and map symbols to INDstocks cash-market IDs."""
    source = local_csv or NIFTY500_URL
    universe = pd.read_csv(source)
    cols = {str(c).strip().lower(): c for c in universe.columns}
    symbol_col = cols.get("symbol")
    name_col = cols.get("company name") or cols.get("company")
    if not symbol_col:
        raise ValueError(f"Could not find a Symbol column in {source}")
    if not name_col:
        name_col = symbol_col
    universe = universe[[symbol_col, name_col]].copy()
    universe.columns = ["symbol", "stock_name"]
    universe["symbol"] = universe["symbol"].astype(str).str.strip().str.upper()
    universe["stock_name"] = universe["stock_name"].astype(str).str.strip()
    universe = universe[universe.symbol.ne("")].drop_duplicates("symbol")
    instrument_response = requests.get(f"{BASE_URL}/market/instruments", params={"source": "equity"},
                                       headers=headers(token), timeout=45)
    instrument_response.raise_for_status()
    instruments = pd.read_csv(StringIO(instrument_response.text))
    required = {"EXCH", "SECURITY_ID"}
    if not required.issubset(instruments.columns):
        raise ValueError(f"Unexpected INDstocks equity instrument columns: {list(instruments.columns)}")
    # Instrument masters may vary in whether the base NSE symbol is exposed
    # as SYMBOL_NAME or TRADING_SYMBOL (often with an -EQ suffix). Build the
    # lookup from both documented fields to avoid a brittle one-column join.
    exch = instruments["EXCH"].astype(str).str.upper().str.replace(r"[^A-Z]", "", regex=True)
    instruments = instruments[exch.str.startswith("NSE")].copy()
    exchange_values = ", ".join(sorted(set(exch.dropna().astype(str)))[:8])
    sample_cols = [c for c in ("SYMBOL_NAME", "TRADING_SYMBOL", "CUSTOM_SYMBOL") if c in instruments.columns]
    master_samples = instruments[sample_cols].head(5).to_dict("records")
    # CSV parsers may infer IDs as floats when blanks occur in other rows;
    # normalize to integer strings before building `NSE_<id>` codes.
    instruments["security_id"] = pd.to_numeric(instruments["SECURITY_ID"], errors="coerce").astype("Int64").astype("string")
    symbol_rows: list[pd.DataFrame] = []
    for col in ("SYMBOL_NAME", "TRADING_SYMBOL", "CUSTOM_SYMBOL"):
        if col not in instruments.columns:
            continue
        part = instruments[[col, "security_id"]].dropna(subset=[col, "security_id"]).copy()
        part["symbol"] = part[col].astype(str).str.strip().str.upper()
        part["symbol"] = part["symbol"].str.replace(r"^(NSE:)|(-EQ|_EQ|\.NS)$", "", regex=True)
        part = part[part.symbol.str.fullmatch(r"[A-Z0-9&.\-]+", na=False)]
        symbol_rows.append(part[["symbol", "security_id"]])
    if not symbol_rows:
        raise ValueError("INDstocks equity master has no usable symbol columns.")
    instruments = pd.concat(symbol_rows, ignore_index=True).drop_duplicates("symbol")
    universe = universe.merge(instruments, on="symbol", how="left")
    universe["scrip_code"] = "NSE_" + universe["security_id"].fillna("")
    missing = universe["security_id"].isna()
    problems = [f"{s}: no NSE cash instrument ID" for s in universe.loc[missing, "symbol"]]
    universe = universe[~missing].reset_index(drop=True)
    if universe.empty:
        raise ValueError(f"No NIFTY 500 constituents matched INDstocks NSE equity instruments. "
                         f"Check the NSE constituent file and token permissions. Exchange values seen: "
                         f"{exchange_values or 'none'}; instrument examples: {master_samples}.")

    index_response = requests.get(f"{BASE_URL}/market/instruments", params={"source": "index"},
                                  headers=headers(token), timeout=45)
    index_response.raise_for_status()
    index_df = pd.read_csv(StringIO(index_response.text), header=None, skiprows=1, names=["exchange", "name", "id"])
    row = index_df[(index_df["exchange"].astype(str).str.upper() == "NSE") &
                   (index_df["name"].astype(str).str.upper().str.strip() == "NIFTY 50")]
    if row.empty:
        raise ValueError("INDstocks instrument master did not contain the NSE NIFTY 50 index.")
    benchmark_id = str(int(row.iloc[0]["id"]))
    benchmark_code = "NSE_" + benchmark_id
    return universe, benchmark_code


def fetch_daily_frames(token: str, scrip_codes: list[str], days: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Fetch daily OHLCV in batches of five and <=364-day windows."""
    auth = headers(token)
    now = datetime.now(IST)
    end_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    # API accepts a maximum one-year window per call. Leave a day of overlap
    # and deduplicate on timestamp when stitching adjacent windows.
    windows: list[tuple[int, int]] = []
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(cursor + 364 * 24 * 60 * 60 * 1000, end_ms)
        windows.append((cursor, window_end))
        cursor = window_end

    accumulated: dict[str, list[dict]] = {code: [] for code in scrip_codes}
    failed: list[str] = []
    for offset in range(0, len(scrip_codes), 5):
        batch = scrip_codes[offset:offset + 5]
        for start, end in windows:
            response = requests.get(f"{BASE_URL}/market/historical/1day",
                                    params={"scrip-codes": ",".join(batch), "start_time": start, "end_time": end},
                                    headers=auth, timeout=45)
            response.raise_for_status()
            payload = response.json()
            if payload.get("success") is not True:
                raise ValueError(f"INDstocks historical data request failed: {payload}")
            data = payload.get("data", {})
            for code in batch:
                candles = data.get(code, {}).get("candles") or []
                if not candles:
                    continue
                accumulated[code].extend(candles)

    frames: dict[str, pd.DataFrame] = {}
    for code, candles in accumulated.items():
        if not candles:
            failed.append(code)
            continue
        raw = pd.DataFrame(candles).drop_duplicates("ts", keep="last").sort_values("ts")
        # API `ts` values are epoch seconds. Convert to IST session dates and
        # use date-only indexes, as the original yfinance scanner does.
        stamps = pd.to_datetime(raw["ts"], unit="s", utc=True).dt.tz_convert(IST)
        raw.index = pd.DatetimeIndex(stamps.dt.normalize().dt.tz_localize(None), name="Date")
        frame = raw.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
        frames[code] = frame[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    return frames, failed
