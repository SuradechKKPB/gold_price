// Gold digest + GTA sync Worker.
//
// Cloudflare's egress reaches BOTH the public world price AND the Thai association API
// (goldtraders.or.th) — the latter 403s GitHub/AWS datacenters but not Cloudflare, so this
// Worker fully retires the phone for gold:
//   - syncGta(): pull GTA /Latest (association bid/ask + spot + fx), upsert to Supabase, so
//     the dashboard + the digest's "ราคาสมาคม (ขายได้จริง)" line stay fresh. Runs hourly
//     during Thai market hours + at each digest.
//   - sendDigest(): the fixed-time LINE card (06:00 / 15:00 ICT), precise to the second.
// Heavy scoring stays in Python on GitHub; this is pure fetch.

const CONV = (15.244 / 31.1034768) * 0.965; // THB per baht-weight of 96.5% bar, per XAU×USDTHB
const VERDICT_TH = { hold: "ถือไว้", trim: "ลดพอร์ตเล็กน้อย", sell_tranche: "ขายบางส่วน", sell: "ขายออก" };
const nf = new Intl.NumberFormat("en-US");
const GTA_HEADERS = {
  "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
  Accept: "application/json, text/plain, */*",
  Referer: "https://www.goldtraders.or.th/",
};

async function jget(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function supaHeaders(env, write) {
  const h = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
  if (write) { h["content-type"] = "application/json"; h["Prefer"] = "resolution=merge-duplicates"; }
  return h;
}

async function supaUpsert(env, table, rows, onConflict) {
  const r = await fetch(`${env.SUPABASE_URL}/rest/v1/${table}?on_conflict=${onConflict}`, {
    method: "POST", headers: supaHeaders(env, true), body: JSON.stringify(rows),
  });
  if (!r.ok) throw new Error(`upsert ${table} ${r.status}: ${(await r.text()).slice(0, 150)}`);
}

// Fetch GTA /Latest and upsert the tick + today's daily row (source 'cf_gta'), preserving
// the intraday open and true high/low across the hourly syncs. Returns the fresh quote.
async function syncGta(env) {
  const d = await jget("https://www.goldtraders.or.th/api/GoldPrices/Latest", { headers: GTA_HEADERS });
  const asTime = d.asTime; // "YYYY-MM-DDTHH:MM:SS" Bangkok wall-clock
  const day = asTime.split("T")[0];
  const sell = d.bL_SellPrice;

  await supaUpsert(env, "gold_price_ticks", [{
    as_time: `${asTime}+07:00`, seq: d.seq ?? 0, bar_buy: d.bL_BuyPrice, bar_sell: sell,
    ornament_buy: d.oM965_BuyPrice, gold9999_buy: d.oM9999_BuyPrice, gold_spot_usd: d.goldSpot,
    baht_per_usd: d.bahtPerUSD, chg_prev_row: d.priceChangeFromPrevRow, chg_prev_day: d.priceChangeFromPrevDayLast,
    gold_price_id: d.goldPriceID,
  }], "as_time,seq");

  const ex = await jget(
    `${env.SUPABASE_URL}/rest/v1/gold_price_daily?select=bar_sell_open,bar_sell_high,bar_sell_low&trade_date=eq.${day}`,
    { headers: supaHeaders(env, false) },
  );
  const prev = ex[0];
  await supaUpsert(env, "gold_price_daily", [{
    trade_date: day,
    bar_sell_open: prev?.bar_sell_open ?? sell,
    bar_sell_high: Math.max(sell, prev?.bar_sell_high ?? sell),
    bar_sell_low: Math.min(sell, prev?.bar_sell_low ?? sell),
    bar_sell_close: sell,
    bar_buy_close: d.bL_BuyPrice,
    gold_spot_usd: d.goldSpot,
    baht_per_usd: d.bahtPerUSD,
    source: "cf_gta",
  }], "trade_date");

  return { day, bar_buy: d.bL_BuyPrice, bar_sell: sell };
}

async function buildMessage(env) {
  const H = supaHeaders(env, false);
  const [sig] = await jget(
    `${env.SUPABASE_URL}/rest/v1/signals_daily?select=trade_date,sell_pressure,verdict&order=trade_date.desc&limit=1`,
    { headers: H },
  );
  const [px] = await jget(
    `${env.SUPABASE_URL}/rest/v1/gold_price_daily?select=bar_buy_close&order=trade_date.desc&limit=1`,
    { headers: H },
  );

  let xau = null, fx = null;
  try { xau = (await jget("https://api.gold-api.com/price/XAU")).price; } catch (e) {}
  try { fx = (await jget("https://open.er-api.com/v6/latest/USD")).rates.THB; } catch (e) {}

  const lines = ["🔔 ราคาทองวันนี้"];
  if (xau && fx) lines.push(`สากล real-time: $${nf.format(Math.round(xau))}/oz ≈ ${nf.format(Math.round(xau * fx * CONV))} บาท/บาททอง`);
  if (px?.bar_buy_close != null) lines.push(`ราคาสมาคมฯ (ขายได้จริง): ${nf.format(Math.round(px.bar_buy_close))} บาท/บาททอง`);
  if (sig?.sell_pressure != null) lines.push(`คะแนนสัญญาณ ${Math.round(sig.sell_pressure)}/100 — ${VERDICT_TH[sig.verdict] || sig.verdict}`);
  if (sig?.trade_date) lines.push(`ข้อมูล ณ ${sig.trade_date}`);
  lines.push(`ดูรายละเอียด: ${env.DASHBOARD_URL}`);
  return lines.join("\n");
}

async function sendDigest(env) {
  try { await syncGta(env); } catch (e) { /* market closed / GTA hiccup: fall back to latest stored price */ }
  const text = await buildMessage(env);
  const resp = await fetch("https://api.line.me/v2/bot/message/broadcast", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`, "content-type": "application/json" },
    body: JSON.stringify({ messages: [{ type: "text", text }] }),
  });
  return { ok: resp.ok, status: resp.status, text };
}

const DIGEST_CRONS = new Set(["0 23 * * *", "0 8 * * *"]);

export default {
  async scheduled(event, env, ctx) {
    if (DIGEST_CRONS.has(event.cron)) {
      ctx.waitUntil(sendDigest(env));         // syncGta runs inside sendDigest first
    } else {
      ctx.waitUntil(syncGta(env).catch(() => {})); // hourly: just keep the association price fresh
    }
  },
  // Manual: /preview?key= (show text) · /send?key= (send now) · /sync?key= (pull GTA now) · /gta?key= (raw)
  async fetch(req, env) {
    const url = new URL(req.url);
    const ok = url.searchParams.get("key") && url.searchParams.get("key") === env.TRIGGER_KEY;
    if (url.pathname === "/preview" && ok) return new Response(await buildMessage(env), { headers: { "content-type": "text/plain; charset=utf-8" } });
    if (url.pathname === "/send" && ok) return new Response(JSON.stringify(await sendDigest(env), null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
    if (url.pathname === "/sync" && ok) {
      try { return new Response(JSON.stringify(await syncGta(env), null, 2), { headers: { "content-type": "application/json" } }); }
      catch (e) { return new Response(JSON.stringify({ error: String(e) }), { status: 200 }); }
    }
    if (url.pathname === "/gta" && ok) {
      try {
        const r = await fetch("https://www.goldtraders.or.th/api/GoldPrices/Latest", { headers: GTA_HEADERS });
        return new Response(JSON.stringify({ status: r.status, snippet: (await r.text()).slice(0, 300) }, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
      } catch (e) { return new Response(JSON.stringify({ error: String(e) }), { status: 200 }); }
    }
    return new Response("gold-digest worker: digests 23:00/08:00 UTC (06:00/15:00 ICT) + hourly GTA sync", { status: 200 });
  },
};
