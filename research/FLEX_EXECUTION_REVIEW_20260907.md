# Flex Execution Contract Review

## Scope

This change does not alter RT formulas, factors, weights, stage thresholds,
sector/ETF mappings, or the satellite -3% / +4% rule.

## Execution

- EOD exits are durable events, not fills. An exact common ETF opening price
  is required before an exit is recorded as model-executed.
- Events retain their entry-cycle identity after a sleeve becomes flat.
- Fixed entry-basket weights determine satellite risk. Account allocation
  changes do not retrospectively rebalance that risk basket.
- The simulator marks the account at each execution instant, sells before
  buying, and charges 1 bp on each actual traded side. Historical funding
  cannot be reconstructed from later cash or later closing valuations.
- Missing execution prices block the entire allocation. Tail executions
  require timestamped instrument prices and cannot use that day's open.
- Local real fills are not generated from model events. Existing simulation
  fills are retained; pre-v6 simulation books are archived verbatim before
  creating an explicitly labeled new baseline, not a historical reconstruction.

## Research Interpretation

The event backtest uses fractional industry-index proxies, not executable
historical ETF fills. It accounts for actual observed price changes and
transaction fees. It does not assume constant daily returns over a holding
period. Open positions remain marked but are not counted as completed trades.

Strict loading disables forward-filled opening prices and close-as-open
substitutions. With the current input files, full-sample replay blocks on
2020-01-20 at the coal opening price; retrospective OOS blocks on 2024-10-11
at the Hang Seng TECH opening price. Their partial trade counts are diagnostic,
not complete performance results. Annualized return and win-rate fields are
suppressed rather than calculated through invented prices.

Historical walk-forward is a fixed-policy temporal replay, not independent
parameter validation. Strict prospective validation remains unverified until
pre-registered policy and point-in-time input archives exist. Historical EOD
tail-entry assumptions are labeled hypothetical and kept separate from the
strict-evidence baseline.

## Remaining Evidence Limits

- Missing historical prices and different underlying-market calendars require
  genuine executable ETF history or explicit instrument-calendar evidence.
- One-basis-point fees do not model spread, slippage, queues, suspensions,
  price limits, or the inability to obtain a specific opening print.
- Frontend ETF lot sizing and industry-proxy fractional research positions
  are intentionally disclosed as different execution bases.
- A model-executed event does not establish that the user actually traded.
- No return improvement or independent out-of-sample validity is claimed.
