# A-Share Risk Thermometer

## What it is

A free-data A-share market risk and fear thermometer. It combines an HS300 index option implied-volatility replica, QVIX confirmation, realized volatility, drawdown pressure, market breadth, and turnover stress into a 0-100 market risk temperature.

## What it is not

It is not an official AVIX/QVIX index.
It is not investment advice.
It is not a trading signal by itself.

## Data sources

- AKShare/Sina options history: `option_cffex_hs300_daily_sina`
- AKShare/Sina realtime option chain: `option_cffex_hs300_spot_sina`
- AKShare/OptBBS QVIX: `index_option_300index_qvix`
- AKShare index history: `stock_zh_index_daily`
- AKShare/Eastmoney A-share breadth: `stock_zh_a_spot_em`
- Shibor, cached by trade date when available

## Methodology

- `AVIX_CLOSE_REPLICA`: historical close-price VIX-style variance replication.
- `AVIX_CLEAN_CLOSE`: price/strike/DTE filtering, moneyness filtering, Black-76 IV filter, rolling-median smile smoothing, and repricing.
- `AVIX_REALTIME_MID`: bid/ask midpoint for current observation when realtime chain is available.
- QVIX is validation and confirmation only (agreement quality, not fear level). It does not overwrite the AVIX replica.
- Realized volatility uses HS300 20-day and 60-day annualized volatility.
- Drawdown pressure combines HS300 and SSE 60-day drawdowns.
- Market breadth uses daily saved A-share snapshots when available; otherwise a wide-index proxy with `WARN_BREADTH_PROXY`.
- Turnover stress uses HS300 volume versus its 20-day average.
- Composite weights are **loaded at runtime** from `config/scoring.yml` (must sum to 1.0).
- AVIX S3/S4 levels (22/25) and quality thresholds are read from `config/thresholds.yml`.

## Related signals on the dashboard

| Signal | Meaning |
|--------|---------|
| Risk temperature | 0–100 composite fear/risk gauge |
| RT research watch | Study rule `60 ≤ RT < 75` + HS300 60d drawdown ≤ −5% (not advice) |
| S3 / S4 | Separate AVIX + SSE price-action rules |

## Deployment

GitHub Pages + GitHub Actions.

- Daily workflow updates data after the A-share close, validates outputs, builds `docs/`, commits, and deploys Pages.
- Realtime AVIX workflow updates nowcast JSON during the session and triggers a Pages deploy when data changes.

## Local commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/bootstrap_history.py --recent-days 120
python scripts/build_site_data.py
python scripts/validate_data.py
python scripts/smoke_test.py
python -c "from tests.test_scoring_config import *; test_weights_sum_to_one(); test_weights_match_defaults(); test_regimes_cover_full_range(); test_thresholds_defaults(); print('config ok')"
```

Open `docs/index.html` after `build_site_data.py`.

### Full rebuild vs incremental

- `python scripts/bootstrap_history.py --full` — full option probe + full AVIX recompute
- `python scripts/update_daily.py` — incremental AVIX for missing dates + short tail
- `python scripts/recalculate_from_cache.py` — recompute from cached options without network (when available)

## Research

Curated reports live under `research/output/` (for example `csi300_risk_temp_strategy_report.md`). Grid strategies with fewer than 20 trades or extreme-panic bare buys are **not** promoted to production.

## Limitations

- No historical bid/ask.
- No historical settlement price.
- No historical open interest.
- Free sources may fail or change.
- If a source fails, the pipeline downgrades `quality` rather than silently treating partial data as full quality.
- Historical breadth is often index-proxy, not full A-share stock breadth.
