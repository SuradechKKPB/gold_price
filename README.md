# gold_price — THB gold sell-timing dashboard

Decision-support tool for timing the **sale** of physical gold priced in THB. Tracks the
Gold Traders Association (GTA) 96.5% bar **buy-in** price, scores daily technical +
fundamental sell-pressure, and backtests exit rules on ~20 years of history.

> Not investment advice. Every signal is a discipline aid; gold is in a strong secular
> uptrend where mean-reversion signals fail often — see the dashboard caveats.

## Architecture

```
data sources ─► GitHub Actions (nightly Python) ─► Supabase Postgres ─► Next.js on Vercel ─► you (LINE alerts)
```

- **ETL/compute** — `etl/` (Python 3.12, uv). Nightly via `.github/workflows/daily-etl.yml`.
- **Store** — Supabase Postgres. Schema in `supabase/migrations/`.
- **Dashboard** — Next.js 15 (added in a later phase), read-only on Vercel.

## Data sources

| Purpose | Source |
| --- | --- |
| THB gold (live + 20yr) | GTA `goldtraders.or.th/api/GoldPrices/{Latest,ohlc}` |
| Real yield / USD / inflation / USDTHB | FRED (`DFII10`, `DTWEXBGS`, `CPIAUCSL`, `DEXTHUS`) |
| ETF flows | SPDR `GLD_US_archive_EN.csv` |
| Positioning | CFTC COT (managed-money net) |

We sell at the GTA **bar buy-in** (`bL_BuyPrice`). The 20yr `/ohlc` history is the bar
**sell-out** series; the ~200 THB spread is modeled as a transaction cost.

## Local dev

```bash
uv sync
cp .env.example .env      # fill in keys (.env is gitignored)
uv run python -m etl.run  # dry-runs without Supabase env; upserts with it
```

## Status

- [x] Phase 1 — data spine: schema, GTA loader, nightly CI
- [ ] Phase 2 — technical indicators + 0–100 sell-pressure score
- [ ] Phase 3 — backtest harness (benchmarks, CPCV, laddered vs DCA-out)
- [ ] Phase 4 — Next.js dashboard
- [ ] Phase 5 — family auth + LINE alerts
