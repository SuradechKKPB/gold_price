// Gold digest + GTA sync Worker.
//
// Cloudflare's egress reaches BOTH the public world price AND the Thai association API
// (goldtraders.or.th) — the latter 403s GitHub/AWS datacenters but not Cloudflare, so this
// Worker fully retires the phone for gold:
//   - syncGta(): pull GTA /Latest (association bid/ask + spot + fx), upsert to Supabase, so
//     the dashboard + the digest's "ราคาสมาคม (ขายได้จริง)" line stay fresh.
//   - sendDigest(): the fixed-time LINE card (06:00 / 15:00 ICT), precise to the second.
// Heavy scoring stays in Python on GitHub; this is pure fetch.
//
// The association price refreshes exactly twice a day, once inside each digest run. Both
// firings come from ONE cron trigger ("0 8,23 * * *") — the Workers-Free ceiling of five
// counts triggers, not firings — so every scheduled invocation here IS a digest.
//
// If intraday freshness is ever wanted, it does NOT need another trigger either: widen
// the expression to cover more hours and branch on event.scheduledTime so only 08:00 and
// 23:00 UTC send, while the rest sync GTA only.

const CONV = (15.244 / 31.1034768) * 0.965; // THB per baht-weight of 96.5% bar, per XAU×USDTHB
// Verdicts of the v4 score (etl/signals.py VERDICTS).
const VERDICT_TH = {
  weak: "ราคาอ่อนตัว (ยังไม่ควรขาย)", neutral: "ปกติ", rich: "โซนแพง", very_rich: "โซนแพงมาก (จังหวะขาย)",
};
const ACTION_TH = { sell: "ขายไม้นี้ได้", wait: "รอก่อน (ราคาอ่อน)", hold: "ถือรอ", done: "ครบตามแผนแล้ว", ended: "จบแผนแล้ว" };
const thDate = (iso) => new Date(`${iso}T00:00:00+07:00`).toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok", day: "numeric", month: "short" });
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
// the intraday open and true high/low across the day's syncs. Returns the fresh quote.
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

/** Distance to the published 40-bar high, measured against the LIVE price when there is one.
 *
 *  In v4 this distance feeds the BRAKE (a tranche due on a weak day may wait), and the
 *  band it is compared with is published as `brake_dd`, so no threshold is copied here.
 *
 *  `dd_from_high` as published by etl.compute is derived from the last stored DAILY CLOSE,
 *  so printing it verbatim beside the real-time line puts two clocks in one message. On
 *  2026-08-31 that gap was 1.26pp and it straddled a threshold: the digest said 4.2%
 *  ("break open") while the live price said 2.90% ("break not open yet"). Wrong side of
 *  the 3% line, not a rounding difference.
 *
 *  The HIGH still comes from the DB — a 40-bar rolling max barely moves intraday, and its
 *  lookback belongs to etl/indicators.py. Only the DISTANCE needs the live price. Both
 *  sides are the same basis: CONV here is 0.472952 and the Python series uses 0.47295.
 *
 *  A live price above the stored high IS a new high, so the high is raised and the distance
 *  is 0 — the same result as the rolling max + clip(lower=0) on the Python side.
 */
function trailFrom(recentHigh, livePrice, ddStored) {
  if (recentHigh == null) return null;
  if (livePrice == null) {
    return ddStored == null ? null : { dd: ddStored, high: recentHigh, live: false };
  }
  const high = Math.max(recentHigh, livePrice);
  return { dd: (high - livePrice) / high, high, live: true };
}

/** Retry-once, never-throw wrapper for a read the card can live without.
 *
 *  Every Supabase read in the digest is OPTIONAL, and until now none of them said so:
 *  buildMessage awaited two bare jget()s, so a single transient PostgREST 5xx threw out
 *  of sendDigest and the whole digest silently evaporated — no card, no failover, no log.
 *  That is not hypothetical: Supabase answered this project with 504 "Gateway Timeout" on
 *  2026-09-13 and took a GitHub compute run down with it. The same blip landing 40 minutes
 *  later would have cost the 06:00 card instead.
 *
 *  A blip is usually over within a second, so try twice; if it is not, drop that ONE line
 *  and send the rest. The live-price lines come from feeds that have nothing to do with
 *  Supabase, so a degraded card still tells the family what gold costs this morning.
 */
async function soft(label, fn) {
  for (let attempt = 1; attempt <= 2; attempt++) {
    try { return await fn(); }
    catch (e) { console.log(`digest: ${label} attempt ${attempt} failed: ${e}`); }
  }
  return null;
}

async function buildMessage(env) {
  const H = supaHeaders(env, false);
  const [sig] = (await soft("signals_daily", () => jget(
    `${env.SUPABASE_URL}/rest/v1/signals_daily?select=trade_date,sell_pressure,verdict&order=trade_date.desc&limit=1`,
    { headers: H },
  ))) ?? [];
  const [px] = (await soft("gold_price_daily", () => jget(
    `${env.SUPABASE_URL}/rest/v1/gold_price_daily?select=bar_buy_close&order=trade_date.desc&limit=1`,
    { headers: H },
  ))) ?? [];

  // The brake state: distance below the 40-bar high, and the band at which a weak day
  // engages the brake. The stored dd is the fallback for when the live feeds are down —
  // see trailFrom().
  const trailOf = async (series) => {
    const [r] = (await soft(series, () => jget(
      `${env.SUPABASE_URL}/rest/v1/macro_daily?select=value&series=eq.${series}&order=trade_date.desc&limit=1`,
      { headers: H },
    ))) ?? [];
    return r?.value ?? null;
  };
  // The sell plan's decision, published by etl.compute under the state sentinel row. It is
  // personal, so it reaches the broadcast only when the ETL says so (plan.broadcast).
  const planOf = async () => {
    const [r] = (await soft("plan_today", () => jget(
      `${env.SUPABASE_URL}/rest/v1/macro_daily?select=source&series=eq.app_state:plan_today&trade_date=eq.2000-01-01`,
      { headers: H },
    ))) ?? [];
    try { return r?.source ? JSON.parse(r.source) : null; } catch (e) { return null; }
  };
  const [ddStored, recentHigh, brakeDd, plan] = await Promise.all([
    trailOf("dd_from_high"), trailOf("recent_high_40"), trailOf("brake_dd"), planOf(),
  ]);

  let xau = null, fx = null;
  try { xau = (await jget("https://api.gold-api.com/price/XAU")).price; } catch (e) {}
  try { fx = (await jget("https://open.er-api.com/v6/latest/USD")).rates.THB; } catch (e) {}

  const liveThb = xau && fx ? xau * fx * CONV : null;
  const trail = trailFrom(recentHigh, liveThb, ddStored);

  const lines = ["🔔 ราคาทองวันนี้"];
  if (xau && fx) lines.push(`สากล real-time: $${nf.format(Math.round(xau))}/oz ≈ ${nf.format(Math.round(xau * fx * CONV))} บาท/บาททอง`);
  if (px?.bar_buy_close != null) lines.push(`ราคาสมาคมฯ (ขายได้จริง): ${nf.format(Math.round(px.bar_buy_close))} บาท/บาททอง`);
  if (sig?.sell_pressure != null) lines.push(`คะแนนจังหวะขาย ${Math.round(sig.sell_pressure)}/100 — ${VERDICT_TH[sig.verdict] || sig.verdict}`);
  if (trail) {
    const asOf = trail.live ? "" : " · ณ ราคาปิด";
    const band = brakeDd == null ? "" : ` · เบรกที่ ${(brakeDd * 100).toFixed(1)}%`;
    lines.push(`ต่ำกว่ายอด 40 วัน ${(trail.dd * 100).toFixed(1)}% (ยอด ${nf.format(Math.round(trail.high))})${band}${asOf}`);
  }
  if (plan?.broadcast && plan.action) {
    const step = plan.action === "done" || plan.action === "ended"
      ? ACTION_TH[plan.action]
      : `ไม้ ${plan.tranche}/${plan.n_tranches} · ครบกำหนด ${thDate(plan.due_date)} · รอบนี้: ${
          plan.action === "sell" && plan.count > 1 ? `ขาย ${plan.count} ไม้ (ใกล้เส้นตาย)` : ACTION_TH[plan.action] ?? plan.action}`;
    lines.push(`แผนขาย: ${step}`);
  }
  if (sig?.trade_date) lines.push(`ข้อมูล ณ ${sig.trade_date}`);
  // Everything above the link is optional now (see soft()), so count what actually made it.
  // A card of nothing but a header and a URL is worse than no card: it spends per-follower
  // quota to tell the family that the pipeline is broken, which the dashboard says better.
  const dataLines = lines.length - 1;
  lines.push(`ดูรายละเอียด: ${env.DASHBOARD_URL}`);
  return { text: lines.join("\n"), dataLines };
}

/** Messages left on an OA's free monthly allowance, or null if LINE won't say.
 *  Purely for the log: the send path still discovers exhaustion from the 429 itself. */
async function quotaLeft(tok) {
  try {
    const H = { Authorization: `Bearer ${tok}` };
    const [q, c] = await Promise.all([
      jget("https://api.line.me/v2/bot/message/quota", { headers: H }),
      jget("https://api.line.me/v2/bot/message/quota/consumption", { headers: H }),
    ]);
    return q?.value == null ? null : q.value - (c?.totalUsage ?? 0);
  } catch (e) { return null; }
}

// Broadcast with OA failover: send from the primary OA; if it fails (esp. 429 = the free
// 300-msg/month quota is exhausted), retry from the secondary OA. Two free OAs ≈ 600/mo.
async function lineBroadcast(env, text) {
  const body = JSON.stringify({ messages: [{ type: "text", text }] });
  const tokens = [
    ["primary", env.LINE_CHANNEL_ACCESS_TOKEN],
    ["fallback", env.LINE_CHANNEL_ACCESS_TOKEN_2],
  ].filter(([, t]) => t);
  let last = { ok: false, oa: null, status: 0 };
  for (const [oa, tok] of tokens) {
    try {
      const resp = await fetch("https://api.line.me/v2/bot/message/broadcast", {
        method: "POST",
        headers: { Authorization: `Bearer ${tok}`, "content-type": "application/json" },
        body,
      });
      if (resp.ok) return { ok: true, oa, status: resp.status };
      // 429 here is the free 300-msg/month allowance running out mid-month. It is the most
      // common way this Worker goes quiet, and it used to leave no trace anywhere.
      console.log(`digest: ${oa} OA refused (${resp.status})${resp.status === 429 ? " — monthly quota spent" : ""}`);
      last = { ok: false, oa, status: resp.status };
    } catch (e) {
      console.log(`digest: ${oa} OA threw ${e}`);
      last = { ok: false, oa, status: -1 };
    }
  }
  return last;
}

async function sendDigest(env) {
  try { await syncGta(env); }
  catch (e) { console.log(`digest: GTA sync failed (${e}) — falling back to the last stored price`); }

  let card = null;
  try { card = await buildMessage(env); }
  catch (e) { console.log(`digest: buildMessage threw ${e}`); }

  if (!card || card.dataLines === 0) {
    console.log("digest: NOT SENT — no data line survived (Supabase and both live feeds down?)");
    return { ok: false, oa: null, status: 0, reason: "no-data" };
  }

  const r = await lineBroadcast(env, card.text);
  const left = r.ok ? await quotaLeft(r.oa === "primary" ? env.LINE_CHANNEL_ACCESS_TOKEN : env.LINE_CHANNEL_ACCESS_TOKEN_2) : null;
  console.log(
    `digest: ${r.ok ? "SENT" : "NOT SENT"} via ${r.oa ?? "no OA"} (status ${r.status}), ` +
    `${card.dataLines} data lines${left == null ? "" : `, ${left} msgs left on that OA`}`,
  );
  return { ...r, text: card.text };
}

export default {
  // Both crons are digests (see the header note on the 5-trigger ceiling). syncGta runs
  // inside sendDigest, so the association price is live in every card.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(sendDigest(env));
  },
  // Manual: /preview?key= (render the card WITHOUT sending) · /sync?key= (pull GTA now) ·
  // /gta?key= (raw upstream response). There is deliberately no send-now route: broadcast
  // bills per follower against a 300/month free quota, so an endpoint that spends it on
  // every request is a liability. /preview covers testing; the cron owns sending.
  async fetch(req, env) {
    const url = new URL(req.url);
    const ok = url.searchParams.get("key") && url.searchParams.get("key") === env.TRIGGER_KEY;
    if (url.pathname === "/preview" && ok) return new Response((await buildMessage(env)).text, { headers: { "content-type": "text/plain; charset=utf-8" } });
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
    return new Response("gold-digest worker: digests 23:00/08:00 UTC (06:00/15:00 ICT); each syncs GTA first", { status: 200 });
  },
};
