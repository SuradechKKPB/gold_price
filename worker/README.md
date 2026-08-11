# gold-digest (Cloudflare Worker)

Sends the daily LINE gold digest at a **precise** time (CF cron fires within seconds;
GitHub Actions could be 5–40 min late). It only READS the score that the GitHub cron
computes into Supabase, fetches the live world price, and broadcasts to LINE.

- **Schedule:** `0 23 * * *` + `0 8 * * *` UTC = **06:00 & 15:00 ICT** (see `wrangler.toml`).
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
printf '%s' "<value>" | npx wrangler@4 secret put TRIGGER_KEY   # any random string, gates the test URL
```

## Test without waiting for cron
```sh
curl "https://gold-digest.suradech-k.workers.dev/preview?key=<TRIGGER_KEY>"   # show message, no send
curl "https://gold-digest.suradech-k.workers.dev/send?key=<TRIGGER_KEY>"      # send now
```

## Division of labour
- **GitHub Actions** (`.github/workflows/daily-etl.yml`): computes the score (pandas) +
  fires event TRANSITION alerts. Runs a compute ~30 min before each digest so the number
  is fresh.
- **This Worker**: the two fixed-time daily digests only.
