"""Tests for multi-source breadth parsers."""
from __future__ import annotations

import pandas as pd

from src.data_sources.akshare_breadth import (
    SOURCE_EM_FENBU,
    SOURCE_SOHU_ZDT,
    parse_sohu_zdt_html,
    summarize_breadth,
)


SAMPLE_SOHU = """
<table>
<tr><th>日期</th><th>涨停只数</th><th>跌停只数</th><th>停牌</th><th>成交额(亿)</th><th>沪市</th><th>深市</th><th>京市</th></tr>
<tr><th>上涨只数</th><th>平盘只数</th><th>下跌只数</th><th>上涨只数</th><th>平盘只数</th><th>下跌只数</th><th>上涨只数</th><th>平盘只数</th><th>下跌只数</th></tr>
<tr>
<td>07/16</td><td>48</td><td>41</td><td>5</td><td>24191.23</td>
<td>931</td><td>61</td><td>1316</td>
<td>1413</td><td>101</td><td>1379</td>
<td>155</td><td>7</td><td>166</td>
</tr>
</table>
"""


def test_parse_sohu_zdt_row():
    df = parse_sohu_zdt_html(SAMPLE_SOHU, default_year=2026)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["trade_date"] == "2026-07-16"
    assert row["source"] == SOURCE_SOHU_ZDT
    assert row["quality"] == "OK"
    # 931+1413+155 = 2499 up; 1316+1379+166 = 2861 down; 61+101+7 = 169 flat
    assert int(row["advancing_count"]) == 2499
    assert int(row["declining_count"]) == 2861
    assert int(row["valid_count"]) == 2499 + 2861 + 169
    assert float(row["limit_down_count"]) == 41


def test_summarize_breadth_rejects_sparse():
    snap = pd.DataFrame({"最新价": [10.0] * 50, "涨跌幅": [1.0] * 50})
    out = summarize_breadth(snap, "2026-07-16")
    assert str(out.iloc[0]["quality"]).startswith("WARN_BREADTH_SPARSE")


def test_summarize_breadth_ok_stock_level():
    n = 1200
    snap = pd.DataFrame(
        {
            "最新价": [10.0] * n,
            "涨跌幅": [1.0] * 700 + [-1.0] * 400 + [-6.0] * 80 + [-10.0] * 20,
            "source": ["PARSE_EM_A_SPOT"] * n,
        }
    )
    out = summarize_breadth(snap, "2026-07-16")
    assert out.iloc[0]["quality"] == "OK"
    assert int(out.iloc[0]["advancing_count"]) == 700
    assert abs(float(out.iloc[0]["advancing_ratio"]) - 700 / 1200) < 1e-9
