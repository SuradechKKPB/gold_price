# Gold THB Sell-Timing — Handoff

_Last updated: 2026-08-25. Owner: Poom (MAKEIO)._

A private tool that times the **sale** of physical Thai gold (96.5% bars) held by Poom
(~700 g). It is an **exit-discipline aid for a position already held**, NOT a price
predictor. A 0–100 "sell-pressure" score with a trim/tranche/sell ladder drives a
dashboard + LINE notifications.

> **Honest framing (from the 2026-07 expert audit):** on a clean, no-look-ahead backtest
> the score's timing edge over simple dollar-cost-averaging-out is only ~52–56% of windows
> (CI brushes 50%). Treat the score as a **DCA accelerator / context signal, not a precise
> top-picker.** Do not cite the old "82% OOS / beats DCA 63%" numbers — they were inflated
> by look-ahead + same-day fills and have been removed.

---

## 1. Code folders (all locations)

| Folder | Purpose | Deploys to |
|---|---|---|
| `~/gold_price` | **Main repo** (git: github.com/SuradechKKPB/gold_price, branch `main`) | — |
| `~/gold_price/etl` | Python scoring engine (pandas). Compute score, alerts, backtest. | GitHub Actions |
| `~/gold_price/web` | Next.js 15 dashboard (TS, Tailwind v4). | Vercel |
| `~/gold_price/worker` | Cloudflare Worker: daily LINE digest + live GTA sync (JS). | Cloudflare |
| `~/gold_price/.github/workflows` | `daily-etl.yml` — the compute + transition-alert cron. | GitHub Actions |
| `~/gold_price/supabase/migrations` | `0001_init.sql` — DB schema (applied once via SQL editor). | Supabase |
| `~/projects/joe-health` | `ingest/scripts/phone_sync.py` — iPhone Garmin sync. **Gold is retired here** (see §7); still required for Garmin. | iPhone (a-Shell) |

Everything gold-related is now in `~/gold_price`. The phone is no longer in the gold loop.

---

## 2. Architecture / data flow

```
 WORLD PRICE (public, reachable from any IP)          THAI ASSOCIATION PRICE (GTA)
   gold-api.com / LBMA (XAU/USD)                         goldtraders.or.th/api
   frankfurter / open.er-api (USD/THB)                   (403s datacenters EXCEPT Cloudflare)
            │                                                     │
            ▼                                                     ▼
   GitHub Actions (etl.compute, pandas)  ◄── Supabase ──►  Cloudflare Worker (gold-digest)
     • intl.fetch_live_intl → world THB       (Postgres)      • syncGta → gold_price_daily/ticks
     • indicators + signals → signals_daily                   • reads score, sends LINE digest
     • transition LINE alerts                                 • 06:00 / 15:00 ICT (precise)
            │                                                     │
            ▼                                                     ▼
        Vercel dashboard (reads Supabase, force-dynamic)     LINE broadcast (OA failover)
        gold-price-gamma.vercel.app                          @514hgwyf → @905fmqos
```

**Score basis = international gold in THB** (`XAU/USD × USD/THB × 0.47295`), stored in
`macro_daily(series='gold_intl_thb')`. The Thai **association bid** (`gold_price_daily.
bar_buy_close`) is the *realized/displayed* price ("ขายได้จริง") and the backtest's realized
price — NOT the signal basis. Local-premium jitter therefore no longer moves the score.

---

## 3. ETL modules (`etl/`, Python 3.12 + uv)

| File | Role |
|---|---|
| `compute.py` | **Cron entrypoint.** Fetch world price → indicators → signals → upsert `signals_daily` → transition alert. Flags: `--full` (rewrite history on formula change), `--digest {am,pm,now}` (legacy; digest now lives in the Worker). |
| `run.py` | Local/live path (fetches GTA from a Thai IP). Rarely used now. |
| `intl.py` | World gold in THB. `fetch_live_intl()` (keyless, datacenter-OK), `topup_live/from_daily`, LBMA×ECB backfill. |
| `indicators.py` | SMA50/200, death-cross, drawdown-from-high, weekly RSI/MACD/Chandelier/Donchian/%B. |
| `signals.py` | The 0–100 composite + verdict. `SCORE_VERSION` (=3), thresholds `T_TRIM/TRANCHE/SELL = 44/52/60`, hysteresis. |
| `dxy.py` | Reconstructed Dollar Index (ECB FX) + the monotone `DOLLAR_SELL` band table. |
| `advice.py` | Personal overlay: local-premium z-score, deadline decay, target/cost framing. Off unless campaign config set. |
| `alerts.py` | LINE broadcast with **OA failover** + verdict-transition + daily-digest builders. |
| `backtest.py` | Sell-the-holding harness (T+1 fills, pre-2020 selection, block-bootstrap CI, ladder policy). |
| `load.py` | Supabase client + fetch/upsert helpers. |
| `state.py` | Tiny KV store over `macro_daily` (no DDL available): `score_version`, `last_alert`, `last_digest`. |
| `config.py` | Env/settings (pydantic). Holding = 700 g bar; sell-campaign fields optional. |

### The score (signals.py)
`composite = 0.40·trend_break + 0.25·overbought + 0.18·momentum + 0.12·dollar + 0.05·seasonality`
- **trend_break**: fresh drawdown-from-recent-high break (loud on a fresh roll-over, fades with age; age resets on each new leg down) + a graded, price-gated secular backstop (below-200DMA / SMA-spread gated on price<SMA50 / 40w-low).
- **momentum**: continuous weekly-MACD depth (below signal + below zero), no overnight step.
- **dollar**: monotone band map `<80:30 · 80–90:40 · 90–100:55 · 100–110:68 · >110:75` (strong USD → lean sell; derived from **pre-2020** data to avoid look-ahead).
- **seasonality**: point-in-time expanding monthly tilt.
- **verdict**: trim ≥44 · tranche ≥52 · sell ≥60 (sell also needs `n_trend ≥ 2`); hysteresis deadband stops flip-flop.

Any formula/constant change → **bump `SCORE_VERSION`**; the next `compute.py` run auto-rewrites the whole `signals_daily` history so the backtest stays single-epoch.

---

## 4. Infrastructure & accounts

| Service | Detail |
|---|---|
| **Supabase** (gold DB) | Project `wdcwhvqjazyvuqzvczlv` (account owning it — NOT the MCP-connected `bejjljlwgpksrhhhpyna` = MAKEIO prod; **never write gold tables there**). Reached via PostgREST + service-role key. No DDL from CLI here (schema applied via dashboard SQL editor). |
| **Vercel** | Project `suradechks-projects/gold-price`, prod alias **gold-price-gamma.vercel.app**. Deploy: `cd web && vercel deploy --prod`. ⚠️ git auto-deploy needs Root Directory = `web` set in dashboard. |
| **Cloudflare** | Worker **gold-digest**, https://gold-digest.suradech-k.workers.dev, account `bd8c811695995b9c36ee321b4a7f81d6` (suradech.k@pontawee.com). wrangler OAuth already authed on this Mac. Free plan: **max 5 cron triggers/account** (at the limit). |
| **GitHub** | github.com/SuradechKKPB/gold_price. `gh` authed as SuradechKKPB. Actions runs the compute cron. |
| **LINE** | Primary OA **@514hgwyf** ("ราคาทอง"); fallback OA **@905fmqos** ("OnePetro"). Both free plan = **300 msgs/month**, broadcast counts per follower. 8 family followers. |

---

## 5. Secrets (names + where — values NOT here)

`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `LINE_CHANNEL_ACCESS_TOKEN`,
`LINE_CHANNEL_ACCESS_TOKEN_2`, `FRED_API_KEY`, `TRIGGER_KEY` (Worker only).

| Store | Holds |
|---|---|
| `~/gold_price/.env` (gitignored) | all of the above except TRIGGER_KEY |
| GitHub Actions secrets | SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, LINE_CHANNEL_ACCESS_TOKEN(_2), FRED_API_KEY |
| Cloudflare Worker secrets | SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, LINE_CHANNEL_ACCESS_TOKEN(_2), TRIGGER_KEY |
| Vercel env | SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (dashboard reads DB) |

The service-role key bypasses RLS (full DB access) — keep it out of git and client code.

---

## 6. Scheduled jobs (all times Asia/Bangkok = UTC+7, no DST)

| When (ICT) | Where | What |
|---|---|---|
| 05:30, 14:30 | GitHub | compute score ~30 min before each digest |
| 13:00, 19:00, 01:00 | GitHub | compute + transition alert (only fires on a verdict change) |
| **06:00, 15:00** | **Cloudflare Worker** | **daily LINE digest** (syncs GTA first → live association price) |

GitHub cron can be 5–40 min late; Cloudflare cron is precise to seconds (that is why the
digest lives there). Association price refreshes 2×/day (at each digest) — a 3rd Worker
cron for hourly intraday sync is blocked by the 5-cron free limit.

---

## 7. LINE messaging

- **Digest** ("🔔 ราคาทองวันนี้"): world real-time + association bid + score + verdict.
  Sent by the Worker at 06:00 / 15:00 ICT.
- **Transition alert**: only when the verdict changes tier. Sent by GitHub `compute.py`.
- **OA failover**: every send tries **@514hgwyf** first; on any failure (esp. HTTP 429 =
  the free 300/month quota is spent) it retries from **@905fmqos**. Two free OAs ≈ 600/mo,
  covering 8 followers × 2 digests/day ≈ 480/mo. ⚠️ **Family must add BOTH OAs as friends**
  to receive during whichever OA is active. Quota resets at the start of each month.

---

## 8. The phone (joe-health) — retired for gold

`phone_sync.py` used to fetch GTA from the iPhone's Thai residential IP (datacenters were
403'd). It broke after an iOS update (the a-Shell time automation). **Gold no longer needs
it**: the Cloudflare Worker reaches GTA directly, and GitHub fetches the world price. The
phone is still required for **Garmin** (joe-health) — that automation fix is parked. See
`~/projects/joe-health/ingest/scripts/PHONE_SETUP.md`.

---

## 9. Runbook (common tasks)

```bash
# --- Score / ETL (from ~/gold_price) ---
.venv/bin/python -m etl.compute            # recompute now (self-fetches world price)
.venv/bin/python -m etl.compute --full     # rewrite full history (after a formula change)
.venv/bin/python -m etl.backtest           # re-run the backtest

# --- Trigger the GitHub cron on demand ---
gh workflow run daily-etl.yml --ref main
gh run list --workflow=daily-etl.yml --limit 5

# --- Cloudflare Worker (from ~/gold_price/worker) ---
npx wrangler@4 deploy                       # deploy
curl "https://gold-digest.suradech-k.workers.dev/preview?key=<TRIGGER_KEY>"   # show digest, no send
curl "https://gold-digest.suradech-k.workers.dev/send?key=<TRIGGER_KEY>"      # send digest now
curl "https://gold-digest.suradech-k.workers.dev/sync?key=<TRIGGER_KEY>"      # pull GTA → Supabase now
printf '%s' "<value>" | npx wrangler@4 secret put <NAME>                       # rotate a secret

# --- Web (from ~/gold_price/web) ---
./node_modules/.bin/next build              # build (pnpm run is gated by sharp; call next directly)
vercel deploy --prod                         # deploy prod

# --- LINE quota check ---
curl -H "Authorization: Bearer <TOKEN>" https://api.line.me/v2/bot/message/quota/consumption
```

Preview dev server locally via the `gold` config in `~/.claude/launch.json` (runs
`next start` on :3017 — it serves a BUILD, so `next build` first).

---

## 10. Known issues / gotchas

- **LINE free quota (300/mo/OA)** is the binding constraint. Failover to a 2nd OA buys ~600/mo;
  beyond that, reduce frequency, push-to-group (1 msg/send), or a paid OA plan.
- **Cloudflare 5-cron/account free limit** blocks hourly intraday GTA sync (2×/day only).
- **GitHub cron delay** (5–40 min) — why the digest moved to Cloudflare.
- **No DDL from here** — schema changes need the Supabase dashboard SQL editor; internal
  state is stored in `macro_daily` via `etl/state.py` instead of a dedicated table.
- **Vercel git auto-deploy** needs Root Directory = `web`; until then deploy via CLI.
- **Score staleness**: the digest's score is as fresh as the last GitHub compute (≤ a few
  hours). The world/association prices in it are live.
- **Timezone**: store UTC, display Asia/Bangkok; convert only at the edges.

---

## 11. Recent major changes (2026)

1. **Score basis → international THB** (removes local-premium jitter).
2. **Expert audit + de-bias**: killed look-ahead (seasonality point-in-time, DXY table
   reversed to monotone/pre-2020, T+1 backtest fills, pre-2020 threshold selection,
   bootstrap CIs), de-jittered the score (continuous momentum, gated death-cross,
   hysteresis), recalibrated thresholds to 44/52/60. Honest edge ≈ 52–56% vs DCA.
3. **Self-sufficient cron**: GitHub fetches the world price itself → no phone for the score.
4. **Cloudflare Worker digest** (precise 06:00/15:00) + **live GTA sync** (CF reaches GTA)
   → phone fully retired for gold; association price fresh.
5. **LINE OA failover** when a free OA's monthly quota is spent.

See `git log` for commit-level detail.
