// Gold digest Worker — sends the daily LINE card at a PRECISE time (Cloudflare cron
// fires within seconds, unlike GitHub Actions). It only READS the score (computed by the
// GitHub cron into Supabase) + fetches the live world price, then broadcasts to LINE.
// Heavy scoring stays in Python on GitHub; this is pure fetch, so it fits the edge.

const CONV = (15.244 / 31.1034768) * 0.965; // THB per baht-weight of 96.5% bar, per XAU×USDTHB
const VERDICT_TH = { hold: "ถือไว้", trim: "ลดพอร์ตเล็กน้อย", sell_tranche: "ขายบางส่วน", sell: "ขายออก" };
const nf = new Intl.NumberFormat("en-US");

async function jget(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

async function buildMessage(env) {
  const H = { apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` };
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
  const text = await buildMessage(env);
  const resp = await fetch("https://api.line.me/v2/bot/message/broadcast", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`, "content-type": "application/json" },
    body: JSON.stringify({ messages: [{ type: "text", text }] }),
  });
  return { ok: resp.ok, status: resp.status, text };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(sendDigest(env));
  },
  // Manual test / preview:  GET /send?key=<TRIGGER_KEY>  sends now;  GET /preview?key=... shows the text.
  async fetch(req, env) {
    const url = new URL(req.url);
    const ok = url.searchParams.get("key") && url.searchParams.get("key") === env.TRIGGER_KEY;
    if (url.pathname === "/preview" && ok) {
      return new Response(await buildMessage(env), { headers: { "content-type": "text/plain; charset=utf-8" } });
    }
    if (url.pathname === "/send" && ok) {
      const r = await sendDigest(env);
      return new Response(JSON.stringify(r, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
    }
    return new Response("gold-digest worker: crons 23:00 & 08:00 UTC (06:00/15:00 ICT)", { status: 200 });
  },
};
