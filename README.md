# NIFTY 500 Signal Studio — INDstocks copy

This is a separate copy of the NIFTY 500 Signal Studio using INDstocks daily historical candles. The source folder and yfinance version are unchanged. The filters, output columns, dashboard style, and CSV download are retained.

## Start the dashboard

Open PowerShell in this folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run dashboard_indstocks.py
```

Paste your INDstocks access token into the password-style field in the sidebar. Generate/copy a token from the INDstocks API Trading access-tokens page. Tokens expire after 24 hours. The app does not write the token to a source or configuration file; it is held in the running Streamlit session and is used for API requests.

Use **Refresh market data** after changing/renewing the token or when you want to clear the 15-minute data cache.

## Command-line scanner

Set the token in the current PowerShell session, then run the CLI:

```powershell
$env:INDSTOCKS_ACCESS_TOKEN = "PASTE_YOUR_TOKEN_HERE"
python scanner_indstocks.py
```

Optional arguments:

```powershell
python scanner_indstocks.py --require-5day-breakout
python scanner_indstocks.py --history-days 728 --output reports\scan.csv
python scanner_indstocks.py --universe-csv nifty500_constituents.csv
```

The default CSV output is `nifty500_scan_indstocks.csv`. A separate `*_data_issues.csv` is written when some symbols have no usable candles or insufficient indicator history.

## Data and crossover details

- The scanner requests NSE equity and NIFTY 50 index instrument IDs from INDstocks, then requests daily OHLCV candles in batches of five.
- The dashboard supports 6 months, 1 year, and 2 years of history. INDstocks caps each daily request window at one year, so longer histories are paged and deduplicated.
- EMA20/EMA50 and the existing RSI/DMI/ADX filters are calculated locally from candle closes. The displayed crossover date is the latest date in the requested lookback where EMA20 changed from at/below EMA50 to above it.
- Your original yfinance version downloads `auto_adjust=True` data. INDstocks returns OHLCV candles and its documented historical response does not advertise an auto-adjustment option. Around splits or cash dividends, EMA values and crossover dates can therefore differ. Compare crossover candles with your charting/broker data and confirm completed daily candles after the NSE close.
- The NIFTY 50 panel checks that OHLC values are internally consistent and that the latest candle is recent. This is not an independent provider comparison; the dashboard recommends checking against your broker.

## INDstocks API references

- [Historical daily candles](https://api-docs.indstocks.com/historicalData/) — `GET /market/historical/1day`, at most five scrip codes per request, at most one year per window.
- [Equity and index instrument masters](https://api-docs.indstocks.com/instruments/) — used to map NSE symbols to `SECURITY_ID` values.
- [Authentication and access tokens](https://api-docs.indstocks.com/Users/) — access tokens go in the `Authorization` header and expire after 24 hours.
