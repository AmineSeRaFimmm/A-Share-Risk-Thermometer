"""Unit tests for CFFEX 日统计·期权 XML → option cache mapping."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data_sources.cffex_option_daily import (
    SOURCE,
    cffex_rtj_xml_url,
    parse_cffex_io_daily_xml,
    write_cffex_io_to_option_cache,
)

SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<dailydatas>
  <dailydata>
    <instrumentid>IF2609</instrumentid>
    <tradingday>20260716</tradingday>
    <openprice>4700</openprice>
    <highestprice>4710</highestprice>
    <lowestprice>4690</lowestprice>
    <closeprice>4705</closeprice>
    <volume>100</volume>
    <productid>IF</productid>
  </dailydata>
  <dailydata>
    <instrumentid>IO2609-C-5300</instrumentid>
    <tradingday>20260716</tradingday>
    <openprice>30.2</openprice>
    <highestprice>30.6</highestprice>
    <lowestprice>25.6</lowestprice>
    <closeprice>27.2</closeprice>
    <settlementpriceif>27.2</settlementpriceif>
    <settlementprice>27.2</settlementprice>
    <volume>71</volume>
    <productid>IO</productid>
  </dailydata>
  <dailydata>
    <instrumentid>IO2609-P-5300</instrumentid>
    <tradingday>20260716</tradingday>
    <openprice></openprice>
    <highestprice></highestprice>
    <lowestprice></lowestprice>
    <closeprice>710.8</closeprice>
    <settlementpriceif>676.2</settlementpriceif>
    <settlementprice>676.2</settlementprice>
    <volume>0</volume>
    <productid>IO</productid>
  </dailydata>
</dailydatas>
"""


def test_cffex_rtj_url_shape():
    assert cffex_rtj_xml_url("2026-07-16") == (
        "http://www.cffex.com.cn/sj/hqsj/rtj/202607/16/index.xml"
    )


def test_parse_io_only_and_fill_blank_ohlc():
    df = parse_cffex_io_daily_xml(SAMPLE_XML, trade_date="2026-07-16")
    assert len(df) == 2
    assert set(df["contract"]) == {"io2609c5300", "io2609p5300"}
    call = df[df["contract"] == "io2609c5300"].iloc[0]
    assert call["date"] == "2026-07-16"
    assert call["month"] == "2026-09"
    assert call["cp"] == "C"
    assert int(call["strike"]) == 5300
    assert float(call["close"]) == 27.2
    assert call["source"] == SOURCE
    put = df[df["contract"] == "io2609p5300"].iloc[0]
    # blank open/high/low filled from close
    assert float(put["open"]) == 710.8
    assert float(put["close"]) == 710.8
    assert float(put["volume"]) == 0.0


def test_write_cache_merges_and_prefers_cffex(tmp_path, monkeypatch):
    from src.data_sources import cffex_option_daily as mod
    from src.storage import paths as path_mod

    monkeypatch.setattr(mod, "RAW", tmp_path)
    monkeypatch.setattr(path_mod, "RAW", tmp_path)
    (tmp_path / "options_daily").mkdir(parents=True)

    # existing Sina row for same day
    sina = pd.DataFrame(
        [
            {
                "date": "2026-07-16",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "contract": "io2609c5300",
                "month": "2026-09",
                "cp": "C",
                "strike": 5300,
                "source": "SINA_AKSHARE",
                "fetch_time": "old",
            }
        ]
    )
    sina.to_csv(tmp_path / "options_daily" / "io2609c5300.csv", index=False)

    day = parse_cffex_io_daily_xml(SAMPLE_XML)
    stats = write_cffex_io_to_option_cache(day)
    assert stats["contracts"] == 2
    merged = pd.read_csv(tmp_path / "options_daily" / "io2609c5300.csv")
    assert len(merged) == 1
    assert float(merged.iloc[0]["close"]) == 27.2
    assert merged.iloc[0]["source"] == SOURCE
    assert (tmp_path / "options_daily" / "io2609p5300.csv").exists()
