import PriceChart from "@/components/PriceChart";
import { BacktestTable, BucketBars, DxyPanel, IndicatorsTable, KeyLevels, PlanPanel, ScoreGauge, TrailStop, TruthFeed, VerdictChip } from "@/components/ui";
import { drawdown, sma } from "@/lib/indicators";
import { computeTA } from "@/lib/ta";
import { fetchTrumpPosts } from "@/lib/trump";
import { bahtWeight, bangkokDate, calDate, newsDate, num, pct, thb } from "@/lib/format";
import { fetchCalendar, fetchNews } from "@/lib/news";
import { fetchRealtimeGold } from "@/lib/realtime";
import { getBacktest, getIntlHistory, getLatestSignal, getLatestTick, getPlanStatus, getPriceHistory, getTrailState } from "@/lib/queries";
import { DXY_TABLE, fetchCurrentDxy } from "@/lib/dxy";

// Decision tool: always render the current score from the DB — never serve a stale
// prerender/fetch-cache (a wrong number here mistimes a real sell).
export const dynamic = "force-dynamic";

const SIGNAL_LABELS: Record<string, string> = {
  brake: "เบรก: ราคาอ่อนตัวจากยอด",
  stretched_above_sma50: "ยืดเหนือเส้น 50 วัน (บน 10%)",
  sharp_rally_from_low: "วิ่งขึ้นแรงจากจุดต่ำ 40 วัน (บน 10%)",
  at_recent_high: "อยู่ที่ยอด 40 วัน",
};

export default async function Page() {
  const grams = Number(process.env.GOLD_GRAMS ?? 1000);
  const bw = bahtWeight(grams);
  const showHolding = process.env.SHOW_HOLDING === "true"; // default: hide personal holding on the public page

  const [signal, tick, prices, intlPrices, runs, news, events, realtime, trump, dxyNow, trail, plan] = await Promise.all([
    getLatestSignal(),
    getLatestTick(),
    getPriceHistory(),
    getIntlHistory(),
    getBacktest(63), // the live plan is a ~3-month campaign
    fetchNews(),
    fetchCalendar(),
    fetchRealtimeGold(),
    fetchTrumpPosts(),
    fetchCurrentDxy(),
    getTrailState(),
    showHolding ? getPlanStatus() : Promise.resolve(null), // personal: hidden on the public page
  ]);

  // Score + technical analysis run on the WORLD gold price in THB (intlPrices); the
  // association bid (buyIn) stays the realized number Poom actually sells at.
  const ta = computeTA(intlPrices, 0);
  const intlClose = intlPrices.at(-1)?.bar_buy_close ?? 0;
  const buyIn = tick?.bar_buy ?? prices.at(-1)?.bar_buy_close ?? 0;
  const holdingValue = bw * buyIn;
  const rtTime = realtime?.asOf
    ? new Date(realtime.asOf).toLocaleTimeString("th-TH", { timeZone: "Asia/Bangkok", hour: "2-digit", minute: "2-digit" })
    : "";

  const priceSeries = intlPrices.map((r) => ({ time: r.trade_date, value: r.bar_buy_close }));
  // Backtest span comes from the data, not a hardcoded year range that silently goes stale.
  const btSpan = intlPrices.length
    ? `${intlPrices[0].trade_date.slice(0, 4)}–${intlPrices[intlPrices.length - 1].trade_date.slice(0, 4)}`
    : "";
  const ma200 = sma(intlPrices, 200);
  const dd = drawdown(intlPrices, "2011-01-01", "2014-12-31");

  const buckets = signal
    ? [
        { label: "เหนือเส้น 50 วัน", value: signal.overbought },
        { label: "วิ่งจากจุดต่ำ", value: signal.momentum },
        { label: "ลึกเทียบเบรก", value: signal.trend_break },
      ]
    : [];

  return (
    <main style={{ maxWidth: 980, margin: "0 auto", padding: "48px 24px 80px" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="serif" style={{ fontSize: 34, fontWeight: 500, letterSpacing: -0.5 }}>
            ทองคำ — ควรขายเมื่อไหร่
          </h1>
          <p className="muted" style={{ marginTop: 4, fontSize: 14 }}>
            ทองคำแท่ง 96.5% · ราคารับซื้อ (บาท) · วิเคราะห์เทคนิค + ปัจจัยพื้นฐาน · ทดสอบย้อนหลัง 20 ปี
          </p>
        </div>
        {tick && (
          <div className="muted mono" style={{ fontSize: 12, textAlign: "right" }}>
            สมาคมฯ ครั้งที่ {tick.seq}
            <br />
            {bangkokDate(tick.as_time)}
          </div>
        )}
      </header>

      {/* Hero */}
      <section className="panel" style={{ padding: 28, marginTop: 28, display: "grid", gap: 28, gridTemplateColumns: "1.1fr 1fr" }}>
        <div>
          <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4 }}>
            ทองสากล · real-time{rtTime ? ` · ${rtTime}` : ""}
          </div>
          <div className="mono serif" style={{ fontSize: 44, marginTop: 8, color: "var(--gold)" }}>
            ฿{thb(realtime ? realtime.thbBar : buyIn)}
            <span className="muted" style={{ fontSize: 18 }}> /บาททอง</span>
          </div>
          <div className="muted mono" style={{ fontSize: 13, marginTop: 6 }}>
            {realtime ? `XAU $${num(realtime.xauUsd)}/oz · USDTHB ${num(realtime.usdThb)}` : "ราคาสมาคมค้าทองคำฯ"}
          </div>
          <div className="muted mono" style={{ fontSize: 13, marginTop: 4 }}>
            ราคาสมาคมฯ (ขายได้จริง): {thb(buyIn)} /บาททอง
            {showHolding ? ` · ${grams} ก. ≈ ฿${thb(holdingValue)}` : ""}
          </div>
          {signal && (
            <div style={{ marginTop: 20, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <VerdictChip verdict={signal.verdict} />
              {signal.active_signals.length > 0 && (
                <span className="muted" style={{ fontSize: 13 }}>
                  {signal.active_signals.map((s) => SIGNAL_LABELS[s] ?? s).join(" · ")}
                </span>
              )}
            </div>
          )}
          {trail && (
            <div style={{ marginTop: 22, paddingTop: 20, borderTop: "1px solid var(--border)" }}>
              <TrailStop trail={trail} price={realtime?.thbBar ?? intlClose} live={realtime != null} />
            </div>
          )}
        </div>
        <div>
          {signal && <ScoreGauge score={signal.sell_pressure} verdict={signal.verdict} />}
          <div className="muted mono" style={{ fontSize: 11, marginTop: 6, textAlign: "right" }}>
            ฐานคะแนน: ราคาทองสากล (THB)
          </div>
          <div style={{ marginTop: 20 }}>
            <BucketBars buckets={buckets} />
          </div>
        </div>
      </section>

      {plan && (
        <section className="panel" style={{ padding: 24, marginTop: 20 }}>
          <PlanPanel plan={plan} />
        </section>
      )}

      {/* Score explained */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          อ่านคะแนนอย่างไร
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.55 }}>
          คะแนนบอกว่า <b style={{ color: "var(--text)" }}>วันนี้เป็นวันขายที่ดีแค่ไหน</b> ไม่ได้บอกว่าควรขายเท่าไหร่ ·
          ความเร็วในการขายเป็นของ<b style={{ color: "var(--text)" }}>แผน</b> (ขายกี่กรัม ภายในเมื่อไหร่) · 100 = ราคาแข็งกว่าทุกวันในรอบ 3 ปี ·
          ≥90 = โซนแพงมาก แผนขายไม้ของช่วงนั้นก่อนกำหนดได้ · ราคาย่อจากยอดเกินเส้นเบรก = วันอ่อน ไม้ที่ครบกำหนดรอได้ ·
          คิดจาก<b style={{ color: "var(--text)" }}>ราคาทองสากลแปลงเป็นบาท</b> (XAU×USDTHB) ส่วนราคาที่ขายได้จริงอิงราคารับซื้อสมาคมฯ
          {signal ? ` · ตัวเลขด้านล่าง = ค่าจริงวันที่ ${signal.trade_date}` : ""}
        </p>
        <div style={{ display: "grid", gap: 16, marginTop: 14 }}>
          {[
            { name: "คะแนนรวม", weight: "0–100", cur: signal?.sell_pressure, desc: "ค่าเฉลี่ยของ percentile สองตัวด้านล่าง เทียบกับทุกวันในรอบ ~3 ปีก่อนหน้า (ไม่แอบเห็นอนาคต) · ทั้งสองตัววัด ‘ราคาแข็ง’ และในข้อมูลปี 2006–2026 วันที่สองตัวนี้สูงคือวันที่ขายได้สูงกว่าราคาเฉลี่ย ±3 เดือนรอบตัว ทั้งในตลาดขาขึ้นและขาลง (คะแนนเดิม v3 ขายได้ต่ำกว่าค่าเฉลี่ยนั้น 3–4%)",
              formula: signal
                ? `= (${signal.overbought.toFixed(0)} + ${signal.momentum.toFixed(0)}) ÷ 2 = ${signal.sell_pressure.toFixed(0)}`
                : "= (percentile เหนือเส้น 50 วัน + percentile วิ่งจากจุดต่ำ 40 วัน) ÷ 2" },
            { name: "เหนือเส้น 50 วัน", weight: "50%", cur: signal?.overbought, desc: "ราคายืดเหนือค่าเฉลี่ย 50 วันมากแค่ไหน เทียบกับรอบ 3 ปี · สูง = ราคาวิ่งนำค่าเฉลี่ยมาก",
              formula: "= percentile(ราคา ÷ SMA50 − 1)" },
            { name: "วิ่งจากจุดต่ำ", weight: "50%", cur: signal?.momentum, desc: "ราคาขึ้นมาจากจุดต่ำสุด 40 วันทำการแรงแค่ไหน · ตัวนี้คือสิ่งที่ v3 ไม่ได้วัด (ส.ค. 2026 ราคาวิ่ง +13.6% ใน 18 วันโดยที่คะแนนเดิมไม่ขยับ)",
              formula: "= percentile(ราคา ÷ ต่ำสุด 40 วัน − 1)" },
            { name: "ลึกเทียบเบรก", weight: "เบรก", cur: signal?.trend_break, desc: "ราคาย่อจากยอด 40 วันมาแค่ไหนเมื่อเทียบกับเส้นเบรก (1σ ของราคาใน 1 เดือน) · 100 = ถึงหรือเลยเส้น · ไม่ได้บวกเข้าคะแนน แต่ถ้าถึงเส้นและคะแนนต่ำกว่า 50 จะนับเป็นวันอ่อน",
              formula: "= min(ระยะจากยอด ÷ เส้นเบรก, 1) × 100" },
          ].map((s) => (
            <div key={s.name} style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
              <div style={{ width: 116, flexShrink: 0 }}>
                <div>
                  <span style={{ fontSize: 14 }}>{s.name}</span>{" "}
                  <span className="muted mono" style={{ fontSize: 11 }}>{s.weight}</span>
                </div>
                <div className="mono" style={{ fontSize: 18, color: "var(--gold)", marginTop: 2 }}>
                  {s.cur != null ? s.cur.toFixed(0) : "—"}
                  <span className="muted" style={{ fontSize: 11 }}>/100</span>
                </div>
              </div>
              <div>
                <p className="muted" style={{ fontSize: 13, lineHeight: 1.55, margin: 0 }}>
                  {s.desc}
                </p>
                <div className="mono" style={{ fontSize: 11, marginTop: 4, color: "var(--blue)", overflowWrap: "anywhere" }}>
                  {s.formula}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Technical indicators + key levels */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          ตัวชี้วัดทางเทคนิค (รายวัน) + แนวราคาสำคัญ
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.55 }}>
          ตัวชี้วัดเสริมที่คำนวณใหม่ทุกวันจาก<b style={{ color: "var(--text)" }}>ราคาทองสากล</b> (ฐานเดียวกับคะแนน) — เป็นบริบทประกอบ (คะแนน 0–100 ด้านบนคือสัญญาณหลักที่ทดสอบย้อนหลังแล้ว)
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 32, marginTop: 16 }}>
          <div>
            <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4, marginBottom: 4 }}>ตัวชี้วัด</div>
            <IndicatorsTable indicators={ta.indicators} />
          </div>
          <div>
            <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4, marginBottom: 12 }}>
              แนวราคาสำคัญ (ระยะถึงจุดทริกเกอร์)
            </div>
            <KeyLevels levels={ta.levels} />
          </div>
        </div>
      </section>

      {/* Price chart */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
            ราคาทองสากล (เทียบเงินบาท) ปี 2006–ปัจจุบัน
          </h2>
          <span className="muted mono" style={{ fontSize: 12 }}>
            เส้นทอง = ค่าเฉลี่ย 200 วัน · ฐานคะแนน
          </span>
        </div>
        <div style={{ marginTop: 12 }}>
          <PriceChart
            price={priceSeries}
            ma200={ma200}
            marker={dd.dropPct < 0 ? { time: dd.troughDate, text: `2013 ${pct(dd.dropPct)}` } : undefined}
          />
        </div>
      </section>

      {/* Backtest */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          ผลทดสอบย้อนหลัง — แผนขาย 3 เดือน 5 ไม้{btSpan ? ` (ปี ${btSpan})` : ""}
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.6 }}>
          จำลองแผนแบบเดียวกับที่ใช้จริงในทุกช่วงเวลา แล้วแยกสองคำถามออกจากกัน ·{" "}
          <b style={{ color: "var(--text)" }}>ทักษะเลือกวัน</b> = ราคาที่ขายได้เทียบค่าเฉลี่ย ±63 วันทำการรอบวันขาย
          (ตัดผลของเทรนด์ออก บวก = ขายได้สูงกว่าราคาแถวนั้น) ·{" "}
          <b style={{ color: "var(--text)" }}>เทียบขายปลายช่วง</b> = ราคาเฉลี่ยเทียบแผนเดียวกันที่ขายทุกไม้ตอนครบกำหนด ·
          วันขายเฉลี่ยยิ่งช้า ยิ่งได้ประโยชน์จากตลาดขาขึ้น (และเสียในตลาดขาลง) ซึ่งไม่ใช่ทักษะ — แถวสุดท้ายแสดงให้เห็น
        </p>
        <div className="muted mono" style={{ fontSize: 11, marginTop: 12 }}>
          เลือกพารามิเตอร์จากแผนที่เริ่มก่อนปี 2020 เท่านั้น · ราคาขาย = ราคารับซื้อสมาคมฯ วันถัดไป (T+1) · ผลจริงคาดว่าน้อยกว่านี้
        </div>
        <div style={{ marginTop: 10 }}>
          <BacktestTable runs={runs} />
        </div>
      </section>

      {/* Dollar Index regime */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          ดัชนีดอลลาร์ (DXY) → ผลตอบแทนทอง 12 เดือนข้างหน้า
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.55 }}>
          สถิติย้อนหลัง (ทองคำบาท 2006–2026): แบ่งตามระดับ DXY แล้วดูผลตอบแทนเฉลี่ย / ขาดทุนเฉลี่ย / ผลตอบแทนต่อ max drawdown ใน
          12 เดือนถัดมา · แสดงเป็นบริบทเท่านั้น ไม่ได้อยู่ในคะแนน v4 (ทดสอบแล้วไม่ช่วยเลือกวันขาย) · ช่วง &lt;80 และ &gt;110 ตัวอย่างน้อย เชื่อถือได้จำกัด
        </p>
        <div style={{ marginTop: 16 }}>
          <DxyPanel table={DXY_TABLE} current={dxyNow} />
        </div>
      </section>

      {/* News & key dates this week */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          ข่าว &amp; วันสำคัญสัปดาห์นี้
        </h2>
        <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 32, marginTop: 16 }}>
          <div>
            <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4, marginBottom: 10 }}>
              ข่าวทองคำล่าสุด
            </div>
            {news.length === 0 && <div className="muted" style={{ fontSize: 13 }}>โหลดข่าวไม่สำเร็จ</div>}
            <div style={{ display: "grid", gap: 12 }}>
              {news.map((n, i) => (
                <a
                  key={i}
                  href={n.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--text)", textDecoration: "none", display: "block" }}
                >
                  <div style={{ fontSize: 14, lineHeight: 1.45 }}>{n.title}</div>
                  <div className="muted mono" style={{ fontSize: 11, marginTop: 3 }}>
                    {n.source}
                    {n.date ? ` · ${newsDate(n.date)}` : ""}
                  </div>
                </a>
              ))}
            </div>
          </div>
          <div>
            <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4, marginBottom: 10 }}>
              วันสำคัญสัปดาห์นี้ (สหรัฐฯ)
            </div>
            {events.length === 0 && <div className="muted" style={{ fontSize: 13 }}>ไม่มีข้อมูลปฏิทิน</div>}
            <div style={{ display: "grid", gap: 10 }}>
              {events.map((e, i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                  <span
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: 999,
                      marginTop: 5,
                      flexShrink: 0,
                      background: e.impact === "High" ? "var(--red)" : "var(--amber)",
                    }}
                  />
                  <div>
                    <div style={{ fontSize: 13.5, lineHeight: 1.4 }}>{e.title}</div>
                    <div className="muted mono" style={{ fontSize: 11, marginTop: 2 }}>
                      {calDate(e.date)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Trump on Truth Social */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          Trump บน Truth Social — โพสต์ที่เกี่ยวกับตลาด
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.55 }}>
          โพสต์ของทรัมป์ขยับราคาทองผ่าน Fed / ภาษี / ดอลลาร์ · กรองเฉพาะที่เกี่ยวกับเศรษฐกิจ-การเงิน · อัปเดตทุกวัน
        </p>
        <div style={{ marginTop: 16 }}>
          <TruthFeed posts={trump} />
        </div>
      </section>

      <footer className="muted" style={{ fontSize: 12, marginTop: 28, lineHeight: 1.6 }}>
        ใช้เพื่อประกอบการตัดสินใจ ไม่ใช่คำแนะนำการลงทุน · ตัวชี้วัดของคะแนนถูกเลือกหลังจากดูข้อมูลทั้งชุด ผลทดสอบย้อนหลัง
        จึงเป็นขอบบน ไม่ใช่ผลที่คาดหวัง · ผลในอดีตไม่รับประกันอนาคต · ฐานคะแนน: ราคาทองสากล (LBMA × USD/THB จาก ECB) · ราคารับซื้อจริง:
        สมาคมค้าทองคำแห่งประเทศไทย · ข่าว: Google News · ปฏิทิน: ForexFactory
      </footer>
    </main>
  );
}
