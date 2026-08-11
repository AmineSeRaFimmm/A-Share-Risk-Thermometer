from __future__ import annotations

import numpy as np
import pandas as pd

import src.core.clean_surface as surface
from src.core.avix_formula import black76_price
from src.core.risk_temperature import compute_risk_temperature


def test_surface_smoothing_keeps_call_and_put_smiles_separate(monkeypatch) -> None:
    strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
    rows = []
    for strike in strikes:
        for cp in ("C", "P"):
            price = black76_price(100.0, strike, 30 / 365, 0.02, 0.20, cp)
            rows.append(
                {
                    "trade_date": "2026-08-10",
                    "expiry_date": "2026-09-09",
                    "strike": strike,
                    "dte": 30,
                    "cp": cp,
                    "price_raw": price,
                    "valid_price": True,
                }
            )
    chain = pd.DataFrame(rows)
    monkeypatch.setattr(
        surface,
        "_implied_vols",
        lambda prices, _forward, _strikes, _t, _rate, cp: np.where(cp == "C", 0.20, 0.80),
    )

    cleaned = surface.clean_option_surface(chain, pd.DataFrame())

    assert cleaned["clean_valid"].all()
    calls = cleaned[cleaned["cp"] == "C"]
    puts = cleaned[cleaned["cp"] == "P"]
    assert np.allclose(calls["clean_price"], calls["price_raw"], rtol=1e-6, atol=1e-6)
    expected_puts = [black76_price(100.0, k, 30 / 365, 0.02, 0.80, "P") for k in strikes]
    assert np.allclose(puts["clean_price"], expected_puts, rtol=1e-6, atol=1e-6)


def test_missing_breadth_contributes_neutral_score_not_previous_day() -> None:
    dates = ["2026-08-07", "2026-08-10"]
    avix = pd.DataFrame({"trade_date": dates, "avix_clean": [20.0, 20.0], "quality": ["OK", "OK"]})
    qvix = pd.DataFrame(
        {
            "trade_date": dates,
            "qvix_confirmation": [50.0, 50.0],
            "qvix_close": [20.0, 20.0],
            "quality": ["OK", "OK"],
        }
    )
    realized = pd.DataFrame({"trade_date": dates, "realized_vol_percentile": [50.0, 50.0]})
    drawdown = pd.DataFrame({"trade_date": dates, "drawdown_pressure": [50.0, 50.0]})
    breadth = pd.DataFrame(
        {"trade_date": [dates[0]], "breadth_pressure": [90.0], "quality": ["OK"]}
    )

    result = compute_risk_temperature(
        avix,
        qvix,
        realized,
        drawdown,
        breadth,
        pd.DataFrame(
            {
                "symbol": ["sh000300", "sh000300"],
                "date": dates,
                "close": [4000.0, 4000.0],
                "volume": [1.0, 1.0],
                "amount": [1.0, 1.0],
            }
        ),
    )

    assert result.loc[result["trade_date"] == dates[0], "market_breadth_pressure"].item() == 90.0
    assert result.loc[result["trade_date"] == dates[1], "market_breadth_pressure"].item() == 50.0
    assert not bool(result.loc[result["trade_date"] == dates[1], "breadth_observed"].item())
