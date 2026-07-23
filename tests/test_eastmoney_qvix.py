from __future__ import annotations

import pandas as pd

from src.data_sources.eastmoney_qvix import (
    SOURCE_EASTMONEY_DELAYED,
    _board_records,
    _decode_jsonp,
)


def test_decode_jsonp_accepts_callback_payload():
    assert _decode_jsonp('callback({"total": 0, "list": []})') == {"total": 0, "list": []}


def test_board_records_maps_eastmoney_best_bid_ask_in_correct_order():
    quote = {
        "dm": "IO2608-C-4700",
        "xqj": 4700,
        "mmpjg": [0, 0, 0, 0, 93.2, 92.6, 0, 0, 0, 0],
        "mmpl": [0, 0, 0, 0, 7, 3, 0, 0, 0, 0],
        "p": 93.2,
        "ccl": 4279,
        "utime": 1784772066,
    }
    put = {
        **quote,
        "dm": "IO2608-P-4700",
        "mmpjg": [0, 0, 0, 0, 107.2, 106.4, 0, 0, 0, 0],
        "p": 106.6,
        "ccl": 2811,
    }
    raw, quote_time = _board_records([{"list": [{"callQt": quote, "putQt": put}]}])

    row = raw.iloc[0]
    assert row["call_bid"] == 92.6
    assert row["call_ask"] == 93.2
    assert row["put_bid"] == 106.4
    assert row["put_ask"] == 107.2
    assert int(row["call_bid_vol"]) == 3
    assert int(row["put_ask_vol"]) == 7
    assert quote_time == pd.Timestamp("2026-07-23T02:01:06Z")


def test_delayed_source_name_is_explicit():
    assert SOURCE_EASTMONEY_DELAYED.endswith("_DELAYED")
