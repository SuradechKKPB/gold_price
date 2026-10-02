# gold_price — THB gold sell-timing dashboard

Decision-support tool for timing the **sale** of physical gold priced in THB. It runs a
sell **plan** (how much, by when: the seller's call) and grades every day 0–100 on the
world gold price in baht, so each tranche goes on a rich day and waits out a weak one.
It shows the Thai association price you actually sell into, and pushes a twice-daily LINE
digest.

> Not investment advice. The day-choice gain the backtest can show is real but small
> (tenths of a percent to ~2% of the sale price per campaign). The pace of the plan moves
> the result far more than the score does. See §3 of [HANDOFF.md](HANDOFF.md).

## Architecture

```
world price (keyless APIs) ─┐
                            ├─► GitHub Actions (pandas) ─► Supabase ─┬─► Vercel dashboard
GTA association price ──────┘        score + plan + event alerts     │
        ▲                                                            │
        └──────── Cloudflare Worker ──────────────────────────────────┘
                  syncs GTA + sends the 06:00/15:00 ICT LINE digest
```

Why the split: goldtraders.or.th 403s datacenter IPs but answers Cloudflare, and
Cloudflare cron fires within seconds while GitHub's can run 5–40 min late. So Cloudflare
owns anything time-critical or GTA-facing; GitHub owns the pandas scoring.

| Piece | Path | Deploys to |
| --- | --- | --- |
| Scoring engine | [etl/](etl/) — Python 3.12, uv | GitHub Actions |
| Dashboard | [web/](web/) — Next.js 15, TS | Vercel |
| Digest + GTA sync | [worker/](worker/) — JS | Cloudflare Workers |
| Schema | [supabase/migrations/](supabase/migrations/) | applied via dashboard SQL editor |

## The score (v4) and the plan

**Score**: `rich = mean(pct_rank(price / SMA50 − 1), pct_rank(price / 40-bar low − 1))`, with
each rank point-in-time over the last ~3 years. It is computed on **international gold in
THB** (`XAU/USD × USD/THB × 0.47295`), not the association quote, so a local premium swing
can't jolt the signal. Verdicts: `very_rich` ≥ 90 · `rich` ≥ 80 · `neutral` ·
`weak` (the **brake**: price ≥ 1σ of one-month volatility below its 40-bar high **and**
rich < 50). Details in [etl/signals.py](etl/signals.py).

**Plan** ([etl/plan.py](etl/plan.py)): sell `PLAN_SELL_GRAMS` in `PLAN_TRANCHES` equal
tranches by `PLAN_DEADLINE`, one per slot. A tranche goes on the first `very_rich` day of its
slot, else at the slot's end. A due tranche may wait up to 10 trading days out of a `weak`
tape, and the deadline never moves. Current plan: ≥ 500 g of 1,000 g by 2026-12-30
(31 Dec is a holiday), in 5 × 100 g. Record each sale with
`python -m etl.plan sold 100 --price 71650`, or with the `record-sale` workflow from the
GitHub app.

Why v4: the v3 composite was a trailing stop. On the days it said trim or sell, the T+1
fill was 1.7–5.0% **below** the centred ±63-day average price, in every regime. On v4's
`very_rich` days the fill was 3.0–4.8% **above** it. The backtest
([etl/backtest.py](etl/backtest.py)) now reports that drift-neutral day skill beside the
campaign edge and the average sale day, so selling later in a bull market cannot pass for
skill. Measurements: §3 of [HANDOFF.md](HANDOFF.md).

Changing any scoring constant means bumping `SCORE_VERSION`; the next run then rewrites
all of `signals_daily` so the backtest never mixes formula vintages.

## Data sources

| Purpose | Source |
| --- | --- |
| World gold (live) | gold-api.com, LBMA fix as fallback |
| World gold (history) | LBMA PM/AM fix |
| USD/THB and DXY basket | frankfurter.dev (ECB), open.er-api.com as fallback |
| Thai association bid/ask | GTA `goldtraders.or.th/api/GoldPrices/Latest` |

All keyless. We sell at the GTA bar buy-in (`bL_BuyPrice`); before the Worker existed that
column was modeled as sell-out minus a flat 200 THB spread, so realized prices in the
deep history are approximate.

## Local dev

```bash
uv sync
cp .env.example .env                          # fill in keys (.env is gitignored)
.venv/bin/python -m etl.compute               # recompute the score now
.venv/bin/python -m etl.compute --full        # rewrite full history after a formula change
.venv/bin/python -m etl.backtest              # re-run the backtest (read-only; --write stores it)
.venv/bin/python -m etl.plan                  # today's plan decision; `sold` / `undo` edit the ledger
.venv/bin/python -m pytest -q tests           # plan, score causality, alerts, harness metrics
cd web && ./node_modules/.bin/next build      # build the dashboard
```

Full runbook, infrastructure map and known issues: [HANDOFF.md](HANDOFF.md).
