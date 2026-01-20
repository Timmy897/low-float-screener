#!/usr/bin/env python3
"""
Low-float stock screener (best-effort).
- Downloads NASDAQ/Other listed symbol lists (US tickers)
- Uses yfinance to fetch float (floatShares / sharesOutstanding fallback)
- Optionally checks Robinhood tradability via public instruments endpoint
- Outputs CSV and prints short list to console

Usage:
  python3 low_float_robinhood.py --cutoff 10000000 --robinhood --output low_float.csv --workers 10 --limit 0

Arguments:
  --cutoff    Float cutoff (default 10000000)
  --robinhood If set, filter to tickers that appear in Robinhood instruments (best-effort)
  --output    CSV output filename (default: low_float.csv)
  --workers   Number of parallel workers (default: 8)
  --limit     Limit number of symbols to process (0 = all)
"""
import argparse
import requests
import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
from tqdm import tqdm

NASDAQ_LISTED_URL = "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://ftp.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
ROBINHOOD_INSTRUMENTS = "https://api.robinhood.com/instruments/?symbol={symbol}"

def download_symbol_list():
    symbols = set()
    for url in (NASDAQ_LISTED_URL, OTHER_LISTED_URL):
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = r.text.splitlines()
        # skip header line, stop at footer (usually 'File Creation Time' or blank)
        for line in lines[1:]:
            if not line or line.startswith("File Creation"):
                break
            parts = line.split("|")
            if parts:
                sym = parts[0].strip()
                if sym and sym != "Symbol":
                    symbols.add(sym)
    return sorted(symbols)

def fetch_float(symbol, timeout=15):
    # returns float_shares (int) or None, plus small metadata
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        # common keys tried:
        float_shares = None
        for k in ("floatShares", "sharesFloat", "float", "sharesOutstanding"):
            v = info.get(k)
            if isinstance(v, (int, float)) and v > 0:
                float_shares = int(v)
                break
        # some tickers return string numbers, attempt conversion
        if float_shares is None:
            # try parsing from info keys that may be strings
            for k in ("floatShares", "sharesOutstanding"):
                v = info.get(k)
                if isinstance(v, str):
                    try:
                        fv = int(float(v.replace(",", "")))
                        if fv > 0:
                            float_shares = fv
                            break
                    except Exception:
                        pass
        return {
            "symbol": symbol,
            "float": float_shares,
            "shortName": info.get("shortName"),
            "exchange": info.get("exchange"),
            "marketCap": info.get("marketCap")
        }
    except Exception:
        return {"symbol": symbol, "float": None, "shortName": None, "exchange": None, "marketCap": None}

def robinhood_has_symbol(symbol):
    try:
        url = ROBINHOOD_INSTRUMENTS.format(symbol=symbol)
        r = requests.get(url, timeout=6)
        if r.status_code != 200:
            return False
        data = r.json()
        results = data.get("results")
        return bool(results)
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", type=int, default=10_000_000, help="Float cutoff (default 10,000,000)")
    parser.add_argument("--robinhood", action="store_true", help="Filter to Robinhood tradable tickers (best-effort)")
    parser.add_argument("--output", default="low_float.csv", help="CSV output file")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers for yfinance calls")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of symbols to process (0=all)")
    args = parser.parse_args()

    print("Downloading US symbol lists from NASDAQ...")
    symbols = download_symbol_list()
    if args.limit > 0:
        symbols = symbols[:args.limit]
    print(f"Symbols to check: {len(symbols)}")

    results = []
    print("Fetching float data via yfinance (this will take time)...")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_float, sym): sym for sym in symbols}
        for f in tqdm(as_completed(futures), total=len(futures)):
            res = f.result()
            results.append(res)

    # Filter by float value
    filtered = []
    for r in results:
        fval = r.get("float")
        if fval is None:
            continue
        if fval <= args.cutoff:
            filtered.append(r)

    # Optionally filter to Robinhood tradable tickers
    if args.robinhood:
        print("Checking Robinhood tradability (best-effort)...")
        rh_filtered = []
        with ThreadPoolExecutor(max_workers=max(4, args.workers)) as ex:
            futures = {ex.submit(robinhood_has_symbol, r["symbol"]): r for r in filtered}
            for f in tqdm(as_completed(futures), total=len(futures)):
                r = futures[f]
                try:
                    ok = f.result()
                except Exception:
                    ok = False
                if ok:
                    rh_filtered.append(r)
        filtered = rh_filtered

    # Sort by float ascending
    filtered.sort(key=lambda x: x["float"] or float("inf"))

    # Write CSV
    with open(args.output, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["symbol", "float", "shortName", "exchange", "marketCap"])
        for r in filtered:
            writer.writerow([r.get("symbol"), r.get("float"), r.get("shortName"), r.get("exchange"), r.get("marketCap")])

    # Print top results
    print(f"\nFound {len(filtered)} tickers with float <= {args.cutoff:,}")
    for r in filtered[:100]:
        print(f"{r['symbol']:10} float={r['float']:,}  exchange={r.get('exchange')}  name={r.get('shortName')}")
    print(f"\nCSV saved to: {args.output}")

if __name__ == "__main__":
    main()