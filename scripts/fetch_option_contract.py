#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_sources.akshare_options import fetch_option_daily

def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: fetch_option_contract.py <contract> <output_csv>")
    contract, output = sys.argv[1], Path(sys.argv[2])
    df = fetch_option_daily(contract)
    if df.empty:
        raise SystemExit(2)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)

if __name__ == "__main__":
    main()
