# gold-digest (Cloudflare Worker)

Two jobs, both of which only Cloudflare can do:

1. **Sync the Thai association price.** `goldtraders.or.th` 403s GitHub/AWS datacenter
   IPs but answers Cloudflare, so this Worker is what keeps `gold_price_daily` and
   `gold_price_ticks` fresh. It replaced an iPhone that used to do this from a Thai
   residential IP.
2. **Send the daily LINE digest** at a *precise* time — CF cron fires within seconds,
   GitHub Actions can be 5–40 min late.

It only *reads* the score that the GitHub cron computes into Supabase. No scoring here.

- **Schedule:** `0 23 * * *` + `0 8 * * *` UTC = **06:00 & 15:00 ICT**. Both are digests,
  and each calls `syncGta()` first — so the association price refreshes exactly twice a
  day. An hourly intraday sync would need a third trigger and this account sits at the
  Workers-Free ceiling of five.
- **URL:** https://gold-digest.suradech-k.workers.dev
- **Account:** suradech.k@pontawee.com (`bd8c811695995b9c36ee321b4a7f81d6`)

## Deploy / update
```sh
cd worker && npx wrangler@4 deploy
```

## Secrets (stored in Cloudflare, never in git)
```sh
printf '%s' "<value>" | npx wrangler@4 secret put SUPABASE_URL
printf '%s' "<value>" | npx wrangler@4 secret put SUPABASE_SERVICE_ROLE_KEY
printf '%s' "<value>" | npx wrangler@4 secret put LINE_CHANNEL_ACCESS_TOKEN
printf '%s' "<value>" | npx wrangler@4 secret put LINE_CHANNEL_ACCESS_TOKEN_2  # failover OA
printf '%s' "<value>" | npx wrangler@4 secret put TRIGGER_KEY   # random string, gates the test routes
```

## Test routes
```sh
curl "https://gold-digest.suradech-k.workers.dev/preview?key=<TRIGGER_KEY>"  # render the card, no send
curl "https://gold-digest.suradech-k.workers.dev/sync?key=<TRIGGER_KEY>"     # pull GTA into Supabase now
curl "https://gold-digest.suradech-k.workers.dev/gta?key=<TRIGGER_KEY>"      # raw upstream response
```

**There is deliberately no send-now route.** LINE broadcast bills per follower against a
300-message/month free quota that the current cadence already consumes in full, so an
endpoint that spends it on every request is a liability. `/preview` renders the identical
text; the cron owns sending.

## Division of labour
- **GitHub Actions** ([../.github/workflows/daily-etl.yml](../.github/workflows/daily-etl.yml)):
  computes the score in pandas and fires event **transition** alerts. Runs ~30 min before
  each digest so the number the Worker reads is fresh.
- **This Worker**: GTA sync + the two fixed-time daily digests.
