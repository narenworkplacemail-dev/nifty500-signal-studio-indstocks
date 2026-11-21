"""NIFTY 500 Signal Studio dashboard powered by INDstocks historical candles."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from indstocks_data import fetch_daily_frames, load_universe
from scanner_indstocks import add_indicators

st.set_page_config(page_title="NIFTY 500 Signal Studio · INDstocks", page_icon="🌷", layout="wide")
st.markdown("""<style>
.stApp { background: linear-gradient(140deg, #fff9f5 0%, #f5f7ff 55%, #f3fbf7 100%); }
[data-testid="stSidebar"] { background: #f2edff; }
[data-testid="stMetric"] { background: #ffffffc9; border: 1px solid #e9e2f3; padding: 14px 16px; border-radius: 16px; }
div.stButton > button { border-radius: 12px; background: #d8f2e7; border: 1px solid #b7e3d0; }
</style>""", unsafe_allow_html=True)


@st.cache_data(ttl=900, show_spinner=False)
def fetch_market_data(token: str, days: int) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[str], str]:
    universe, benchmark_code = load_universe(token)
    codes = universe["scrip_code"].tolist() + [benchmark_code]
    frames, failed = fetch_daily_frames(token, codes, days)
    return universe, frames, failed, benchmark_code


def fmt_date(value: object) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert(None)
    return stamp.strftime("%Y-%m-%d")


def run_scan(universe: pd.DataFrame, frames: dict[str, pd.DataFrame], *, ema_fast: int, ema_slow: int,
             rsi_min: float, rsi_max: float, adx_min: float, require_di: bool, require_breakout: bool,
             require_recent_cross: bool, cross_window: int) -> tuple[pd.DataFrame, dict[str, int]]:
    found: list[dict] = []
    counts = {key: 0 for key in ("data", "adx_di", "ema", "rsi", "breakout", "cross", "matched")}
    for item in universe.itertuples(index=False):
        raw = frames.get(item.scrip_code)
        if raw is None or not {"High", "Low", "Close"}.issubset(raw.columns):
            continue
        counts["data"] += 1
        df = add_indicators(raw)
        close = df["Close"].astype(float)
        df["EMA20"] = close.ewm(span=ema_fast, adjust=False, min_periods=ema_fast).mean()
        df["EMA50"] = close.ewm(span=ema_slow, adjust=False, min_periods=ema_slow).mean()
        df = df.dropna(subset=["Close", "EMA20", "EMA50", "RSI14", "ADX14", "DI+14", "DI-14"])
        if len(df) < 6:
            continue
        row = df.iloc[-1]
        if not (row["ADX14"] > adx_min and (not require_di or row["DI+14"] > row["DI-14"])):
            continue
        counts["adx_di"] += 1
        if not (row["Close"] > row["EMA20"] > row["EMA50"]):
            continue
        counts["ema"] += 1
        if not (rsi_min <= row["RSI14"] <= rsi_max):
            continue
        counts["rsi"] += 1
        prev5 = df["Close"].iloc[-6:-1]
        breakout = bool(row["Close"] > prev5.max())
        if require_breakout and not breakout:
            continue
        counts["breakout"] += 1
        cross_mask = (df["EMA20"].shift(1) <= df["EMA50"].shift(1)) & (df["EMA20"] > df["EMA50"])
        positions = [i for i, yes in enumerate(cross_mask.fillna(False).tolist()) if yes]
        last_cross = fmt_date(df.index[positions[-1]]) if positions else ""
        if require_recent_cross and (not positions or len(df) - 1 - positions[-1] > cross_window):
            continue
        counts["cross"] += 1
        counts["matched"] += 1
        found.append({
            "Stock name": item.stock_name, "Symbol": item.symbol, "Data date": fmt_date(df.index[-1]),
            "Close": round(float(row["Close"]), 2), f"EMA {ema_fast}": round(float(row["EMA20"]), 2),
            f"EMA {ema_slow}": round(float(row["EMA50"]), 2),
            f"EMA{ema_fast} crossed above EMA{ema_slow} on": last_cross or "No cross in lookback",
            "5-session close pass?": "YES" if breakout else "NO",
            "Previous 5 closes (oldest → newest)": ", ".join(f"{float(x):.2f}" for x in prev5),
            "RSI 14": round(float(row["RSI14"]), 2), "ADX 14": round(float(row["ADX14"]), 2),
            "DI+ 14": round(float(row["DI+14"]), 2), "DI- 14": round(float(row["DI-14"]), 2),
        })
    result = pd.DataFrame(found)
    if not result.empty:
        result = result.sort_values(["ADX 14", "RSI 14"], ascending=[False, True]).reset_index(drop=True)
    return result, counts


st.title("🌷 NIFTY 500 Signal Studio")
st.caption("Daily trend scanner · Powered by INDstocks · Original yfinance scanner remains separate")
with st.sidebar:
    st.header("🔑 INDstocks connection")
    token = st.text_input("Access token", type="password", help="Paste your INDstocks access token. It expires after 24 hours.")
    st.caption("Create/copy a token from your INDstocks API Trading access-tokens page. The app does not write it into a file.")
    st.header("🎛️ Scanner settings")
    st.caption("Filters run in order: ADX/DI → EMA trend → RSI → optional filters.")
    ema_fast = st.number_input("Fast EMA", min_value=2, max_value=100, value=20, step=1)
    ema_slow = st.number_input("Slow EMA", min_value=3, max_value=250, value=50, step=1)
    rsi_min = st.number_input("RSI lower bound", min_value=0.0, max_value=100.0, value=50.0, step=1.0)
    rsi_max = st.number_input("RSI upper bound", min_value=0.0, max_value=100.0, value=75.0, step=1.0)
    adx_min = st.number_input("Minimum ADX 14", min_value=0.0, max_value=100.0, value=25.0, step=1.0)
    require_di = st.toggle("Require DI+ > DI−", value=True)
    require_breakout = st.toggle("Require close above all previous 5 closes", value=False)
    require_recent_cross = st.toggle("Require a recent bullish EMA cross", value=False)
    cross_window = st.number_input("Cross must be within sessions", min_value=1, max_value=250, value=20,
                                   step=1, disabled=not require_recent_cross)
    history = st.selectbox("History for EMA warmup / cross date", ["6mo", "1y", "2y"], index=1)
    refresh = st.button("🔄 Refresh market data", use_container_width=True)
    if refresh:
        fetch_market_data.clear()
        st.rerun()

if ema_fast >= ema_slow or rsi_min > rsi_max:
    st.warning("Adjust the EMA and RSI bounds before scanning.")
    st.stop()
if not token:
    st.info("Paste your INDstocks access token in the sidebar to load the scanner.")
    st.stop()
days = {"6mo": 184, "1y": 364, "2y": 728}[history]
try:
    with st.spinner("Loading NSE instruments and daily candles from INDstocks…"):
        universe, prices, failed, benchmark_code = fetch_market_data(token, days)
except Exception as exc:
    st.error(f"INDstocks data could not be loaded: {exc}")
    st.info("Check the token is current and try Refresh market data.")
    st.stop()

st.subheader("🧭 Data reliability check — NIFTY 50 daily candle")
index_frame = prices.get(benchmark_code)
if index_frame is None or index_frame.empty:
    st.error("INDstocks did not return NIFTY 50 daily OHLC. Benchmark verification is unavailable.")
else:
    valid_frame = index_frame.dropna(subset=["Open", "High", "Low", "Close"])
    candle = valid_frame.iloc[-1]
    candle_date = fmt_date(valid_frame.index[-1])
    o, h, l, c = (float(candle[k]) for k in ["Open", "High", "Low", "Close"])
    valid_ohlc = h >= max(o, c) and l <= min(o, c) and h >= l
    closes = valid_frame["Close"]
    change = ((float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100) if len(closes) > 1 else 0.0
    age_days = (pd.Timestamp.now(tz="Asia/Kolkata").date() - pd.Timestamp(candle_date).date()).days
    q = st.columns([1.1, 1, 1, 1, 1, 1.1])
    for col, label, value in zip(q, ["Session", "Open", "High", "Low", "Close", "Close change"],
                                 [candle_date, f"{o:,.2f}", f"{h:,.2f}", f"{l:,.2f}", f"{c:,.2f}", f"{change:+.2f}%"]):
        col.metric(label, value)
    if valid_ohlc and age_days <= 4:
        st.success(f"✅ OHLC internally consistent · Source: INDstocks · Candle age: {age_days} calendar day(s). Compare values with your broker for an independent check.")
    elif valid_ohlc:
        st.warning(f"Latest daily bar is {age_days} calendar days old. Refresh or check the source before relying on the scan.")
    else:
        st.error("OHLC consistency check failed; inspect the source data before relying on this scan.")
st.caption("The OHLC check validates internal candle consistency. Compare with your broker for an independent source check.")
if failed:
    with st.expander(f"⚠️ {len(failed)} symbols did not return usable INDstocks data"):
        st.write(", ".join(failed))

if st.button("✨ Run NIFTY 500 scan", type="primary", use_container_width=True):
    with st.spinner("Applying your selected filters…"):
        results, counts = run_scan(universe, prices, ema_fast=int(ema_fast), ema_slow=int(ema_slow),
                                   rsi_min=float(rsi_min), rsi_max=float(rsi_max), adx_min=float(adx_min),
                                   require_di=require_di, require_breakout=require_breakout,
                                   require_recent_cross=require_recent_cross, cross_window=int(cross_window))
    st.session_state["scan_results"] = results
    st.session_state["scan_counts"] = counts
    st.session_state["scan_time"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

if "scan_results" in st.session_state:
    results, counts = st.session_state["scan_results"], st.session_state["scan_counts"]
    st.subheader("📊 Scan dashboard")
    m = st.columns(5)
    for col, label, value in zip(m, ["NIFTY 500 constituents", "Stocks with daily data", "Passed ADX / DI", "Passed EMA + RSI", "Final matches"],
                                 [len(universe), counts["data"], counts["adx_di"], counts["rsi"], counts["matched"]]):
        col.metric(label, value)
    st.caption(f"Last scan: {st.session_state['scan_time']} · History: {history} · interval: 1 day · data: INDstocks")
    if results.empty:
        st.info("No stocks matched the selected filters. Try adjusting the sidebar values.")
    else:
        st.dataframe(results, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Download CSV report", results.to_csv(index=False).encode("utf-8-sig"),
                           file_name="nifty500_scan_indstocks.csv", mime="text/csv")
st.divider()
st.caption("For completed-session signals, refresh after the NSE close. This scanner is for screening and verification, not investment advice.")
