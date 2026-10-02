# Gold THB Sell-Timing — Handoff

_Last updated: 2026-10-02. Owner: Poom (MAKEIO)._

A private tool that times the **sale** of physical Thai gold (96.5% bars) held by Poom
(**1,000 g**). It is an **exit-discipline aid for a position already held**, NOT a price
predictor. A sell **plan** sets the pace (≥ 500 g by 2026-12-30 in 5 × 100 g). A 0–100
score grades each day as rich or weak, so every tranche goes on a good day. Both drive a
dashboard and LINE notifications.

> **Honest framing.** The pipeline is mechanically causal: a shock injected on one day
> moves no score on any earlier date (tests/test_signals.py). The v4 inputs were chosen
> after looking at the full 2006–2026 series (the 2026-10-02 audit). The thresholds and the
> brake multiple were chosen on pre-2020 campaigns only. Treat every backtested number as an
> **upper bound**.
>
> What the harness can say (§3): the score's sell days are richer than the prices around
> them in every regime. Inside the plan, that buys **+0.02% to +2.0%** of sale price over a
> no-skill seller, depending on regime and horizon. That is real but small. The plan's
> pace moves the result far more, and the tool deliberately does not decide pace for you.
> Do not cite the old "82% OOS / beats DCA 63%" or "51–56% vs DCA" numbers. Both measured a
> v3 ladder whose edge came from holding longer through a bull market (§11).

---

## 1. Code folders (all locations)

| Folder | Purpose | Deploys to |
|---|---|---|
| `~/gold_price` | **Main repo** (git: github.com/SuradechKKPB/gold_price, branch `main`) | — |
| `~/gold_price/etl` | Python scoring engine (pandas). Compute score, alerts, backtest. | GitHub Actions |
| `~/gold_price/web` | Next.js 15 dashboard (TS, Tailwind v4). | Vercel |
| `~/gold_price/worker` | Cloudflare Worker: daily LINE digest + live GTA sync (JS). | Cloudflare |
| `~/gold_price/.github/workflows` | `daily-etl.yml` — the compute + event-alert cron. `record-sale.yml` — log a sale into the plan's ledger (manual, phone-friendly). | GitHub Actions |
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
     • plan decision + event LINE alerts                      • 06:00 / 15:00 ICT (precise)
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
| `compute.py` | **Cron entrypoint.** Fetch world price → top up DXY (context only) → `score_and_publish`: indicators → signals → upsert `signals_daily` → publish brake state → plan decision → event alert. Flag: `--full`. `run.py` calls the same `score_and_publish`, so the two cannot drift. The digest lives in the Worker. |
| `run.py` | Manual GTA ingest, off-schedule. Kept for a hand-run backfill or repair from a machine that reaches GTA; the Worker owns routine ingest. |
| `intl.py` | World gold in THB. `fetch_live_intl()` (keyless, datacenter-OK), `topup_live/from_daily`, LBMA×ECB `backfill()` (idempotent — re-run to re-finalize history onto true fixes), `bkk_today()`. `load_intl_daily()` drops weekend bars (a repeat of Friday would stretch every rolling window). |
| `indicators.py` | Daily only: SMA50/200, `ext50`, `rally_from_low` (vs 40-bar low), `dd_from_high` (vs 40-bar high), `vol_m` (one-month σ). |
| `signals.py` | The v4 score + verdict. `SCORE_VERSION` (=4), `RANK_WINDOW=756`, `RICH/VERY_RICH = 80/90`, `BRAKE_K=1.0`, hysteresis 5. |
| `plan.py` | **The sell plan.** Pure `decide()` (replayed verbatim by the backtest), live `evaluate()`, the sales ledger, and a CLI: `python -m etl.plan [sold G --price P [--date D] \| undo]`. |
| `dxy.py` | Reconstructed Dollar Index (ECB FX) + the `DOLLAR_SELL` band study. **No longer in the score**; kept as dashboard context. `topup()` still refreshes the tail on the cron. |
| `advice.py` | Execution notes: local-premium z-score and target/cost framing (both optional). The v3 "deadline decay" moved into `plan.py`. |
| `alerts.py` | LINE broadcast with **OA failover** + the event alert (market enters `very_rich`; plan steps only if `PLAN_IN_BROADCAST`). |
| `backtest.py` | v4 harness: drift-neutral day skill + plan campaigns vs slot-end / slot-middle benchmarks, per regime, pre-2020 grid selection, acceptance rule. Read-only unless `--write`. |
| `load.py` | Supabase client + fetch/upsert helpers, incl. `fetch_all()` and `upsert_macro()` for derived series (`series` is free text — a new one needs no migration). |
| `state.py` | Tiny KV store over `macro_daily` under a sentinel date: `score_version`, `market_alert`, `plan_alert`, `plan_sales`, `plan_today`. A hack, but isolated behind `get_state`/`set_state` — see §10. |
| `config.py` | Env/settings (pydantic). Holding = 1,000 g bar; plan = 500 g / 5 tranches / 2026-10-02 → 2026-12-30 (31 Dec is a holiday); `plan_in_broadcast=False`. |

### The score (signals.py, v4)

`rich = mean( pct_rank(price / SMA50 − 1), pct_rank(price / 40-bar low − 1) )`, 0–100. Each
rank compares today only with the ~3 years up to today (point-in-time).

- **very_rich** ≥ 90: the plan sells the current slot's tranche early.
- **rich** ≥ 80: worth watching; no action on its own.
- **weak** = the **brake**: drawdown from the 40-bar high ≥ `BRAKE_K × one-month σ` (clipped
  3–12%) **and** rich < 50. A tranche due on a weak day may wait up to 10 trading days.
- **neutral** otherwise. Tiers are left only 5 points below their entry line (hysteresis).

`signals_daily` kept its v3 columns (no migration): `sell_pressure` = rich, `overbought` =
the SMA50 rank, `momentum` = the rally rank, `trend_break` = brake depth (100 = at or past
the band), `seasonality`/`fa_score` = NULL. The band itself is published as
`macro_daily(series='brake_dd')`.

> **Why v3 was replaced (2026-10-02 audit).** v3 was a trailing stop: 58% of its weight
> (trend_break + weekly MACD) was zero or bullish at a high, and loud only after the drop.
> Measured with the drift-neutral test (T+1 fill vs the centred ±63-day average price, intl
> close), **by regime 2006–11 bull / 2011–18 bear / 2019–26 bull**:
>
> | Days | Fill vs surrounding average |
> |---|---|
> | v3 trim+ (≥44) | −5.0% / −2.7% / −1.7% |
> | v3 verdict = sell | −4.6% / −4.0% / −4.6% |
> | **v4 very_rich** | **+4.6% / +4.8% / +3.0%** |
> | v4 weak (brake) | −3.8% / −1.6% / −2.1%: the days the brake declines to sell on |
>
> The case study: on **2026-08-24 at 71,850 (ask)** v3 read **22.7 / hold**. Its weekly MACD
> had flipped bullish on the 21st and cut 10 points while price made a new high. v4 reads
> **91 / very_rich** that day. v3's loudest `sell` run (2026-07-16→29, 63,396–64,807) sat at
> the local low. v4 reads 6–28 there.
>
> The 2026-08-31 rejection of an overbought re-weight ("57–68% of near-high fires are
> followed by a higher price in 3 months") had no base rate: 61% of all days (70% since
> 2020) are, and v3's own trim/sell days scored 65% on the same test.
>
> The old harness's "parity with DCA" was timing, not skill. In 2019–26, 56% of 12-month
> windows never reached trim and so sold everything at the window end. The ladder's edge
> correlated 0.65 with "sell all at the end" and turned negative in the 2011–18 bear.

**Campaign results (deployed config, pre-2020 selection, realized at the association bid
T+1).** Edge of the plan + score over a no-skill seller (same tranches, random day in each
slot), by regime:

| Campaign | 06–11 bull | 11–18 bear | 19–26 bull | n_eff |
|---|---|---|---|---|
| 3 months (the live shape) | +0.57% | +0.02% | +0.18% | ~78 |
| 6 months | +1.03% | +0.05% | +0.37% | ~38 |
| 12 months | +2.02% | +0.19% | +0.32% | ~19 |

These numbers are small, and that is the finding. Most tranches go at slot end because a
very_rich day does not come in every slot. The gain is concentrated in the tranches that
catch one. For comparison, "sell everything at the end" beats the slot-end plan by
+1.8% / −0.3% / +1.7% over 3 months: pace is a trend bet that dwarfs day choice. That is why
pace is the seller's decision, written into the plan, and not the score's.

**Grid** (`backtest.GRID`, chosen on campaigns starting before 2020): rank window
{all-history, 756} × trigger {rich, very_rich} × brake k {1.0, 1.5} × grace {5, 10}. Choice:
756 / very_rich / 1.0 / 10. Accept rule: day skill > 0 **and** edge vs random day > 0 in all
three regimes. The deployed config passes at 3, 6 and 12 months. Run
`python -m etl.backtest --select` to reproduce.

Any formula/constant change → **bump `SCORE_VERSION`**; the next `compute.py` run auto-rewrites
the whole `signals_daily` history so the backtest stays single-epoch.

**Where the remaining look-ahead is:** the choice of the two inputs (made after the audit
looked at 2006–2026), and the rich/very_rich lines sitting at round percentiles. The
thresholds, window, brake and grace are pre-2020 choices. Fills are T+1.

### The plan (plan.py)

The window (`plan_start` → `plan_deadline`, weekdays) is cut into `plan_tranches` equal
slots. `decide(t, …)` on day t's close says what the next session should do:

1. `done` once every tranche is sold.
2. `sell / deadline` when the decision days left ≤ tranches outstanding. The deadline never moves.
3. `sell / rich` when the verdict is very_rich, the current slot's tranche (or a delayed
   one) is unsold, and ≥ 5 days have passed since the last early sale.
4. Once the slot's due day has passed: `wait / brake` if weak and fewer than 10 days late,
   else `sell / due`.
5. Otherwise `hold / ahead`.

The ledger (`plan_sales`) is the truth about what was sold. Partial tranches count in grams.
The decision is published as `plan_today` and read by the dashboard (`SHOW_HOLDING=true`)
and by the digest (only if `PLAN_IN_BROADCAST`). Live schedule (latest session per
tranche): 21 Oct · 6 Nov · 25 Nov · 11 Dec · 30 Dec 2026. Thai holidays are not modelled.
If a due session falls on one and the shop is shut, sell the session before.

---

## 4. Infrastructure & accounts

| Service | Detail |
|---|---|
| **Supabase** (gold DB) | Project `wdcwhvqjazyvuqzvczlv` (account owning it — NOT the MCP-connected `bejjljlwgpksrhhhpyna` = MAKEIO prod; **never write gold tables there**). Reached via PostgREST + service-role key. DDL is possible via the dashboard SQL editor (Poom has the credentials); nothing currently needs it. |
| **Vercel** | Project `suradechks-projects/gold-price`, prod alias **gold-price-gamma.vercel.app**. Deploy: `cd web && vercel deploy --prod`. ⚠️ git auto-deploy needs Root Directory = `web` set in dashboard. |
| **Cloudflare** | Worker **gold-digest**, https://gold-digest.suradech-k.workers.dev, account `bd8c811695995b9c36ee321b4a7f81d6` (suradech.k@pontawee.com). wrangler OAuth already authed on this Mac. Free plan: **max 5 cron triggers/account**. gold-digest uses **1** (`0 8,23 * * *` — one trigger, two firings; the ceiling counts triggers, not firings). |
| **GitHub** | github.com/SuradechKKPB/gold_price. `gh` authed as SuradechKKPB. Actions runs the compute cron. |
| **LINE** | Primary OA **@514hgwyf** ("ราคาทอง"); fallback OA **@905fmqos** ("ราคาทอง V2"). Both free plan = **300 msgs/month**, broadcast counts per follower. **Followers are NOT symmetric: 8 on the primary, 5 on the fallback** (measured 2026-09-14 via `/v2/bot/insight/followers`). The 3 who follow only the primary go dark the moment failover happens — see §7. |

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
| 13:00, 19:00, 01:00 | GitHub | compute + plan decision + event alert (fires only on a new event) |
| **06:00, 15:00** | **Cloudflare Worker** | **daily LINE digest** (syncs GTA first → live association price) |

GitHub cron can be 5–40 min late; Cloudflare cron is precise to seconds (that is why the
digest lives there). Association price refreshes 2×/day (at each digest) — a 3rd Worker
cron expression is a single trigger (`0 8,23 * * *`) firing twice, so it costs one slot
of the account's five.

All five GitHub slots write `macro_daily` rows keyed by the **Bangkok** calendar date via
`intl.bkk_today()`. Do not reintroduce a naive `pd.Timestamp.today()` there: the runner is
UTC, and the 22:30 and 18:00 UTC slots fire on the *next* Bangkok day, so a naive date
silently overwrites the previous day's close with a later price.

---

## 7. LINE messaging

- **Digest** ("🔔 ราคาทองวันนี้"): world real-time + association bid + score + verdict +
  distance to the 40-bar high beside the brake band. If `PLAN_IN_BROADCAST`, it adds one
  plan line (tranche k/n, due date, this session's action, no grams). Sent by the Worker at
  06:00 / 15:00 ICT. Reports **both** prices by design — the self-computed world price and
  the association announcement.
- **Event alert** (GitHub `compute.py`): when the verdict **enters `very_rich`**, measured at
  ~2.4 times a year. If `PLAN_IN_BROADCAST`, also on each plan step (a tranche becomes
  sellable, waits on the brake, the plan completes). Both share one message when they land
  together. Plan steps stay off the broadcast by default: it reaches every follower, and the
  plan is Poom's own. The v3 per-transition alert (`last_alert`) is retired.
- **OA failover**: every send tries **@514hgwyf** first; on any failure (esp. HTTP 429 =
  the free 300/month quota is spent) it retries from **@905fmqos**. Two free OAs ≈ 600/mo.
  ⚠️ **Family must add BOTH OAs as friends** to receive during whichever OA is active.
  Quota resets at the start of each month.

**Budget (re-measured 2026-09-14 — the old "5 followers" figure was stale):** the primary
OA now has **8** followers, so a day costs 8 × 2 digests = **16 messages**, and its 300/mo
allowance is spent in **~18.75 days**, not 30. Observed: 216/300 consumed by 13 Sep, i.e.
the wall lands about **19 Sep** and the same thing happens every month.

That by itself is survivable — failover moves the send to @905fmqos. What is NOT survivable
is that **only 5 of the 8 followers are on the fallback OA**, so from the wall to month-end
**3 people receive nothing at all**. This already happened once: the primary ran dry on
**24 Aug 2026** (no digest went out that day from either OA — the failover did not exist
yet, it was committed the next morning), and from **25 Aug** the fallback carried the month
at 10 msgs/day, i.e. reaching 5 people instead of 8. Delivery counts per OA per day are
readable from `/v2/bot/insight/message/delivery?date=YYYYMMDD`.

Two ways out, in order of preference:
1. **`push` to a single LINE group** instead of `broadcast`. A group push bills as **one**
   message no matter how many people are in it: 2/day = ~60/month against a 300 allowance,
   with room for event alerts and for the family to grow. This removes the per-follower
   cliff permanently and is the fix this section has recommended since it was written. It
   needs a `groupId`, which only arrives on a webhook `join` event when the OA is invited
   into a group, so it cannot be done from the repo alone.
2. **Get the 3 primary-only followers onto @905fmqos as well** — *chosen 2026-09-14*.
   Invite link: `https://line.me/R/ti/p/@905fmqos`. This does not remove the monthly
   cliff, but it makes failover reach all 8, which is the part that actually hurt. Check
   it landed with the `insight/followers` call in §9: the fallback should read 8, not 5.
   Note the arithmetic afterwards — at 8 followers each OA lasts ~18.75 days, so the two
   together cover ~37 days and the month closes with headroom. A **ninth** follower breaks
   that (2 × 300 ÷ 18 = 33 days is fine, but the margin is thin), so option 1 is still the
   answer the moment the audience grows again.

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
.venv/bin/python -m etl.backtest           # v4 harness, read-only (prints per-regime tables + ACCEPT)
.venv/bin/python -m etl.backtest --select  # also re-run the pre-2020 grid search
.venv/bin/python -m etl.backtest --write   # replace backtest_runs (what the dashboard shows)
.venv/bin/python -m pytest -q tests        # plan rules, score causality, alerts, harness metrics

# --- Sell plan ---
.venv/bin/python -m etl.plan                             # today's decision + progress (read-only)
.venv/bin/python -m etl.plan sold 100 --price 71650      # record a sale (date = today BKK, or --date)
.venv/bin/python -m etl.plan undo                        # remove the last recorded sale
gh workflow run record-sale.yml -f grams=100 -f price=71650   # same, from anywhere (or the GitHub app)
.venv/bin/python -m etl.intl               # re-finalize gold_intl_thb onto true LBMA fixes
.venv/bin/python -m etl.dxy                # reproduce the DOLLAR_SELL anchor table

# --- Trigger the GitHub cron on demand ---
gh workflow run daily-etl.yml --ref main
gh run list --workflow=daily-etl.yml --limit 5

# --- Cloudflare Worker (from ~/gold_price/worker) ---
npx wrangler@4 deploy                       # deploy
curl "https://gold-digest.suradech-k.workers.dev/preview?key=<TRIGGER_KEY>"   # show digest, no send
curl "https://gold-digest.suradech-k.workers.dev/sync?key=<TRIGGER_KEY>"      # pull GTA → Supabase now
# (no /send route by design — broadcast spends a quota that is already fully committed)
printf '%s' "<value>" | npx wrangler@4 secret put <NAME>                       # rotate a secret

# --- Web (from ~/gold_price/web) ---
./node_modules/.bin/next build              # build (pnpm run is gated by sharp; call next directly)
vercel deploy --prod                         # deploy prod

# --- "LINE stopped working" — the three questions, in order ------------------
# 1. Is the token alive and how much allowance is left?  (400/401 = dead token)
curl -H "Authorization: Bearer <TOKEN>" https://api.line.me/v2/bot/info
curl -H "Authorization: Bearer <TOKEN>" https://api.line.me/v2/bot/message/quota         # {"value":300}
curl -H "Authorization: Bearer <TOKEN>" https://api.line.me/v2/bot/message/quota/consumption
# 2. Did messages actually go out, and on which day did they stop?  Run it for BOTH OAs:
#    apiBroadcast = messages delivered that day = followers × sends. Lags ~1 day.
curl -H "Authorization: Bearer <TOKEN>" \
  "https://api.line.me/v2/bot/insight/message/delivery?date=20260913"
# 3. How many people would a send from this OA actually reach?
curl -H "Authorization: Bearer <TOKEN>" \
  "https://api.line.me/v2/bot/insight/followers?date=20260912"
#    Primary and fallback DO NOT have the same followers — see §7.

# Watch a digest fire live (crons at 23:00 / 08:00 UTC); every send now logs its outcome:
cd worker && npx wrangler@4 tail --format json
```

Dev server: the `web` config in this repo's `.claude/launch.json` runs `pnpm --dir web dev`
on **:3000**.

---

## 10. Known issues / gotchas

- **LINE free quota (300/mo/OA)** is the binding constraint, and at 8 followers the cadence
  now spends one full OA in ~19 days, not 30 (see §7). Failover covers the rest of the
  month but only reaches the 5 people who follow the fallback OA, so **3 followers go dark
  from ~19th to month-end, every month.** The durable fix is push-to-group (1 msg/send
  regardless of headcount); the stopgap is getting those 3 onto the fallback OA too.
- **A LINE channel that goes quiet cannot report its own silence.** Both senders now print
  every outcome (which OA served, the status code, messages left) and `etl.compute` exits
  non-zero when a real event alert could not be delivered, so a dropped alert shows
  up as a red GitHub Actions run instead of a cheerful "No alert." in a green log. Check
  delivery from the outside with the two LINE endpoints in §9.
- **Association price refreshes 2×/day only.** Not a cron-count limit: gold-digest now
  fits both digests in one trigger, so slots are free. Going more frequent means widening
  the cron expression and branching on `event.scheduledTime` so only 08:00/23:00 UTC
  broadcast — otherwise every extra firing also spends LINE quota.
- **GitHub cron delay** (5–40 min) — why the digest moved to Cloudflare.
- **State lives in `macro_daily`** under sentinel date `2000-01-01`, series `app_state:*`,
  because DDL was unavailable when it was written. DDL is available now, but the migration
  was deliberately skipped: `etl/state.py` already isolates it behind two functions, and a
  half-applied migration would break alert dedup and double-send into a full quota.
- **Fix/spot seam in `gold_intl_thb`.** History is the LBMA 15:00 London fix; days from
  mid-2026 on are the session close from spot. They differ by whatever gold does after the
  fix — 3.2% on 2026-08-28. `backfill()` refuses to overwrite a spot-recorded day so each
  bar keeps one basis, but the one-off boundary is real and the 40-day rolling high behind
  `dd_from_high` straddles it until ~Oct 2026. No free source covers 20 years of closes.
- **The association price is only as fresh as the last Worker sync** (06:00 / 15:00 ICT),
  so the dashboard can show a quote up to ~9 h old. `etl.run`, or the sync in §9, pulls it
  on demand from a Thai IP.
- **`backtest_runs.median_thb` scales with `GOLD_GRAMS`** — change the holding and the
  stored runs are stale until `etl.backtest --write` is re-run.
- **`GOLD_GRAMS` lives in four places**: `etl/config.py` (default, what GitHub uses), the
  local `.env`, `web/.env.local`, and the Vercel env (the dashboard's holding line). All four
  read 1000 after 2026-10-02 except Vercel, which has to be checked in its dashboard.
- **`web/lib/dxy.ts` `DXY_TABLE` is a hand-copied snapshot** of `etl/dxy.py` output.
  Re-running the study means updating it by hand.
- **A stale `macro_daily` series degrades the score silently**, with no error: `signals`
  ffills whatever was last stored. This bit `dxy`, which nothing on the cron refreshed —
  it sat 3 weeks stale until `dxy.topup()` was wired into `compute.py` (2026-08-31). Any
  new macro series the score joins needs a top-up on the cron, not just a backfill.
- **A published indicator beside a live price is two clocks in one view.** `dd_from_high`
  is computed from the last stored daily close; the dashboard hero and the digest both
  carry a real-time price. Rendering the stored `dd` next to them disagreed by 1.26pp and
  straddled the 3% break threshold (fixed 2026-08-31 — `trailFrom()` in the Worker and
  `TrailStop` in the dashboard now derive the distance from the live price and read only
  the HIGH from the DB). Any future published indicator shown beside a live number needs
  the same treatment.
- **The brake band is published, the rich lines are mirrored.** v3 hand-copied its 3%/8%
  band into the dashboard. v4 publishes the band as `macro_daily(series='brake_dd')`, and
  the dashboard and Worker read it. The two gauge ticks (`RICH_LINE` / `VERY_RICH_LINE` in
  `web/components/ui.tsx`) still mirror `signals.RICH/VERY_RICH`. They are display-only (the
  verdict and its colour come from the DB), but they go stale if those constants move.
- **Merging to main deploys the ETL; web and Worker deploy by hand.** The cron runs whatever
  is on main, so an ETL change that NULLs or renames a column the live page reads must ship
  AFTER the web that tolerates it. v4 shipped in that order (web, then Worker, then merge,
  2026-10-02): the v3 dashboard called `fa_score.toFixed()`, which v4 writes as NULL. The
  temporary v3 fallbacks in the Worker and web were removed once `signals_daily` was v4.
- **The plan is personal and the dashboard is public.** `PlanPanel` renders only with
  `SHOW_HOLDING=true` (Vercel env), and the broadcast carries the plan only with
  `PLAN_IN_BROADCAST=true`. Either switch exposes the plan to everyone who can read that surface.
- **Vercel git auto-deploy** needs Root Directory = `web`; until then deploy via CLI.
- **Score staleness**: the digest's score is as fresh as the last GitHub compute (≤ a few
  hours). The world/association prices in it are live.
- **Timezone**: store UTC, display Asia/Bangkok; convert only at the edges.

---

## 11. Recent major changes (2026)

00. **2026-10-02 — v4: sell into strength, pace by plan.** Poom's report: at 71,850
   (24 Aug) his gut said trim, while the score said 22.7 / hold. The audit found v3 was not
   merely late. Its trim/sell days filled 1.7–5.0% below the surrounding ±63-day average in
   every regime, and its backtest edge was holding longer (§3). Changes: (a) score v4 = rank
   of price vs SMA50 + rank of rally from the 40-bar low, over a 3-year point-in-time
   window. Weekly MACD, the DXY band and seasonality are out. (b) the trailing stop became
   a **brake** (a weak day delays a due tranche ≤ 10 days) with a volatility-scaled band.
   (c) `etl/plan.py`: Poom's pace (≥ 500 g of 1,000 g by 2026-12-30, 5 tranches) with a
   ledger, a CLI and a `record-sale` workflow. (d) the harness was rewritten to replay
   `plan.decide` and to separate day skill from drift. Parameters were chosen on pre-2020
   only, and all three regimes pass. (e) alerts fire when the market enters very_rich; plan
   steps fire only if broadcast. (f) holding 900 → 1,000 g. `SCORE_VERSION` 3 → 4. The next
   run rewrites `signals_daily`.
0. **2026-08-31 — trailing-stop visibility + DXY top-up.** Two findings, both measured, no
   scoring constant touched (`SCORE_VERSION` still 3). (a) Nothing on the cron refreshed
   `macro_daily(series='dxy')` — `backfill_macro()` rewrites 2006-today and was manual-only,
   so the series sat 3 weeks stale while `signals` ffilled it forward, silently pinning the
   dollar sub-score; `dxy.topup()` now refreshes the tail every run. (b) The score's
   structural inability to fire at a high was quantified (see §3) and answered with
   visibility rather than a refit: `dd_from_high` and `recent_high_40` are published to
   `macro_daily` by `compute.py` and rendered by the dashboard's `TrailStop` panel and the
   LINE digest. **Both render the distance against the LIVE price, not the published `dd`** —
   the published value is derived from the last stored daily close, and printing it beside a
   real-time figure put two clocks in one message: it read 4.2% (break open) while the live
   price gave 2.90% (break not open), the wrong side of the 3% line. Only the HIGH is read
   from the DB. Falls back to the stored `dd`, labelled `ณ ราคาปิด`, when the live feeds are
   down. A weight re-fit toward `overbought` was evaluated and **rejected** — it
   fires at highs but 57–68% of those fires are followed by a higher price 3 months later.
1. **2026-08 audit**: holding corrected 700 g → **900 g**; `intl.topup_live`
   now stamps **Bangkok** dates (the UTC runner had been overwriting the prior day's close
   from two of five cron slots — a T+1 leak); `_block_boot` switched from an LCG stride to
   a seeded RNG after a coverage test showed its nominal 95% interval covering the truth
   only 77% of the time (85% after); `n_eff()` added and printed beside every interval;
   unreachable Worker branch, the `/send` route, `compute.py --digest` and
   `alerts.send_daily_digest` deleted; docs reconciled to the code. Verified by shock
   injection that no indicator or sub-score reads the future.
2. **Score basis → international THB** (removes local-premium jitter).
3. **Expert audit + de-bias**: killed look-ahead (seasonality point-in-time, DXY table
   reversed to monotone/pre-2020, T+1 backtest fills, pre-2020 threshold selection,
   bootstrap CIs), de-jittered the score (continuous momentum, gated death-cross,
   hysteresis), recalibrated thresholds to 44/52/60. (The "52–56%" figure published at
   the time was superseded by the 2026-08 bootstrap fix — see item 0.)
4. **Self-sufficient cron**: GitHub fetches the world price itself → no phone for the score.
5. **Cloudflare Worker digest** (precise 06:00/15:00) + **live GTA sync** (CF reaches GTA)
   → phone fully retired for gold; association price fresh.
6. **LINE OA failover** when a free OA's monthly quota is spent.

See `git log` for commit-level detail.
