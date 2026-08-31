# gold_price — THB gold sell-timing dashboard

Decision-support tool for timing the **sale** of physical gold priced in THB. Scores daily
sell-pressure 0–100 on the world gold price expressed in baht, shows the Thai association
price you actually sell into, and pushes a twice-daily LINE digest.

> Not investment advice. See [HANDOFF.md](HANDOFF.md) for the honest read on what the
> backtest can and cannot establish — the short version is that ~19 years of history holds
> only ~17 independent 12-month windows, which is not enough to prove the score beats
> simply dollar-cost-averaging out.

## Architecture

```
world price (keyless APIs) ─┐
                            ├─► GitHub Actions (pandas) ─► Supabase ─┬─► Vercel dashboard
GTA association price ──────┘        score + transition alerts       │
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

## The score

`0.40·trend_break + 0.25·overbought + 0.18·momentum + 0.12·dollar + 0.05·seasonality`,
computed on **international gold in THB** (`XAU/USD × USD/THB × 0.47295`) — not the
association quote, so a local premium swing can't jolt the signal. Verdict ladder:
trim ≥44, tranche ≥52, sell ≥60 (sell also needs 2 trend confirmations), with a hysteresis
deadband. Details and the calibration caveat live in [etl/signals.py](etl/signals.py).

It is a **trailing stop, so it fires after the turn and never at the high** — `trend_break`
is 40% of the weight and is zero until price is 3% off the recent high, which caps the
composite near ~39 against a trim line of 44. Over 2007–2026 no bar at a new high ever
scored above 35.9. The distance to that high is therefore published separately
(`dd_from_high`) and shown on the dashboard and in the digest; see §3 of
[HANDOFF.md](HANDOFF.md) for the measurements and why re-weighting was rejected.

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
.venv/bin/python -m etl.backtest              # re-run the backtest
cd web && ./node_modules/.bin/next build      # build the dashboard
```

Full runbook, infrastructure map and known issues: [HANDOFF.md](HANDOFF.md).
