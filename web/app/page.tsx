import PriceChart from "@/components/PriceChart";
import { BacktestTable, BucketBars, DxyPanel, IndicatorsTable, KeyLevels, ScoreGauge, TruthFeed, VerdictChip } from "@/components/ui";
import { drawdown, sma } from "@/lib/indicators";
import { computeTA } from "@/lib/ta";
import { fetchTrumpPosts } from "@/lib/trump";
import { bahtWeight, bangkokDate, calDate, newsDate, num, pct, thb } from "@/lib/format";
import { fetchCalendar, fetchNews } from "@/lib/news";
import { fetchRealtimeGold } from "@/lib/realtime";
import { getBacktest, getIntlHistory, getLatestSignal, getLatestTick, getPriceHistory } from "@/lib/queries";
import { DXY_TABLE, fetchCurrentDxy } from "@/lib/dxy";

export const revalidate = 60;

const SIGNAL_LABELS: Record<string, string> = {
  trailing_stop_fired: "เบรกจากจุดสูงสุด (trailing stop)",
  secular_confirm: "ยืนยันเทรนด์ขาลงระยะยาว",
  below_200dma: "หลุดเส้นค่าเฉลี่ย 200 วัน",
  death_cross: "เดธครอส 50/200",
  below_40w_low: "หลุดจุดต่ำสุด 40 สัปดาห์",
  rsi_weekly_gt70: "RSI รายสัปดาห์ > 70",
  stretch_gt18pct: "สูงกว่าเส้น 200 วัน > 18%",
  pctb_gt1: "เหนือกรอบบอลลินเจอร์บน",
  macd_bearish: "MACD รายสัปดาห์เป็นขาลง",
};

export default async function Page() {
  const grams = Number(process.env.GOLD_GRAMS ?? 700);
  const bw = bahtWeight(grams);
  const showHolding = process.env.SHOW_HOLDING === "true"; // default: hide personal holding on the public page

  const [signal, tick, prices, intlPrices, runs, news, events, realtime, trump, dxyNow] = await Promise.all([
    getLatestSignal(),
    getLatestTick(),
    getPriceHistory(),
    getIntlHistory(),
    getBacktest(252),
    fetchNews(),
    fetchCalendar(),
    fetchRealtimeGold(),
    fetchTrumpPosts(),
    fetchCurrentDxy(),
  ]);

  // Score + technical analysis run on the WORLD gold price in THB (intlPrices); the
  // association bid (buyIn) stays the realized number Poom actually sells at.
  const ta = computeTA(intlPrices, 0);
  const buyIn = tick?.bar_buy ?? prices.at(-1)?.bar_buy_close ?? 0;
  const holdingValue = bw * buyIn;
  const rtTime = realtime?.asOf
    ? new Date(realtime.asOf).toLocaleTimeString("th-TH", { timeZone: "Asia/Bangkok", hour: "2-digit", minute: "2-digit" })
    : "";

  const priceSeries = intlPrices.map((r) => ({ time: r.trade_date, value: r.bar_buy_close }));
  const ma200 = sma(intlPrices, 200);
  const dd = drawdown(intlPrices, "2011-01-01", "2014-12-31");

  const buckets = signal
    ? [
        { label: "เบรกเทรนด์", value: signal.trend_break },
        { label: "ซื้อมากเกินไป", value: signal.overbought },
        { label: "โมเมนตัม", value: signal.momentum },
        { label: "ดอลลาร์ (DXY)", value: signal.fa_score },
        { label: "ฤดูกาล", value: signal.seasonality },
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
        </div>
        <div>
          {signal && <ScoreGauge score={signal.sell_pressure} />}
          <div className="muted mono" style={{ fontSize: 11, marginTop: 6, textAlign: "right" }}>
            ฐานคะแนน: ราคาทองสากล (THB)
          </div>
          <div style={{ marginTop: 20 }}>
            <BucketBars buckets={buckets} />
          </div>
        </div>
      </section>

      {/* Score explained */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          อ่านคะแนนอย่างไร
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.55 }}>
          0 = ถือ, 100 = แรงกดดันขายสูงสุด · ออกแบบให้ <b style={{ color: "var(--text)" }}>คะแนนพุ่งตอนราคาเพิ่งหลุดจากจุดสูงสุด</b> (จังหวะ ‘capture the high’)
          แล้วค่อยๆ จางลงเมื่อราคาตกไปลึกและนานแล้ว — มีตัวยืนยันเทรนด์ขาลงระยะยาวกันพลาดกรณีตลาดหมีจริง · คะแนนและตัวชี้วัดคิดจาก
          <b style={{ color: "var(--text)" }}>ราคาทองสากลแปลงเป็นบาท</b> (XAU×USDTHB) ไม่ใช่ราคาสมาคม จึงไม่สะดุดเวลาพรีเมียมในประเทศแกว่ง — ส่วนราคาที่ขายได้จริงยังอิงราคารับซื้อสมาคมฯ{signal ? ` · ตัวเลขด้านล่าง = ค่าจริงวันที่ ${signal.trade_date}` : ""}
        </p>
        <div style={{ display: "grid", gap: 16, marginTop: 14 }}>
          {[
            { name: "คะแนนรวม", weight: "0–100", cur: signal?.sell_pressure, desc: "ภาพรวมแรงกดดันให้ขาย — เกณฑ์: ≥33 เริ่มลดพอร์ต · ≥42 ขายบางส่วน · ≥50 (พร้อมสัญญาณเบรกเทรนด์ ≥2 ตัว) ขายออก",
              formula: signal
                ? `= 0.40×${signal.trend_break.toFixed(0)} + 0.25×${signal.overbought.toFixed(0)} + 0.18×${signal.momentum.toFixed(0)} + 0.12×${signal.fa_score.toFixed(0)} + 0.05×${signal.seasonality.toFixed(0)} = ${signal.sell_pressure.toFixed(0)}`
                : "= 0.40×เบรกเทรนด์ + 0.25×ซื้อมากเกินไป + 0.18×โมเมนตัม + 0.12×ดอลลาร์ + 0.05×ฤดูกาล" },
            { name: "เบรกเทรนด์", weight: "40%", cur: signal?.trend_break, desc: "ราคาเพิ่งหลุดจากจุดสูงสุดล่าสุดหรือยัง — ดังสุดตอน ‘เบรกสดๆ’ ใกล้ยอด แล้วจางลงเมื่อขาลงเก่าและลึก (ไม่ไล่ขายที่ก้น) · บวกตัวยืนยันขาลงระยะยาว (หลุด 200DMA + เดธครอส + ต่ำสุด 40 สัปดาห์) ที่ไม่จาง กันถือยาวจนตลาดหมีจริง",
              formula: "= 0.70×(ความแรงเบรก × ความสดของเบรก) + 0.30×(ยืนยันขาลงระยะยาว) · เบรกเปิดเมื่อราคา −3% จากยอด, อิ่มตัวที่ −8%, ความสดจางตามอายุของเบรก" },
            { name: "ซื้อมากเกินไป", weight: "25%", cur: signal?.overbought, desc: "รวมตัวชี้วัดที่บอกว่าราคา ‘ยืดเกิน’ — RSI รายสัปดาห์, ระยะห่างเหนือเส้น 200 วัน, Bollinger %B, ผลตอบแทน 1 ปี · สูง = เสี่ยงย่อ แต่ขาขึ้นแรงอาจค้างสูงได้นาน จึงใช้เป็นสัญญาณ ‘รัดสตอป’ มากกว่าขายทันที",
              formula: "= ค่าเฉลี่ย(clip 0–100): %เหนือ200DMA÷26% · (RSI14wk−50)÷30 · (%B−0.5)÷0.5 · ROC252วัน÷50%" },
            { name: "โมเมนตัม", weight: "18%", cur: signal?.momentum, desc: "MACD รายสัปดาห์: ต่ำกว่าเส้น signal (+50) และต่ำกว่าเส้นศูนย์ (+50) · 50 = เริ่มเป็นขาลง, 100 = ขาลงเต็มตัว",
              formula: "= (MACDwk < signal ? 50 : 0) + (MACDwk < 0 ? 50 : 0)" },
            { name: "ดอลลาร์ (DXY)", weight: "12%", cur: signal?.fa_score, desc: "ระดับ Dollar Index บ่งทิศทางทองในบาท 12 เดือนข้างหน้า (สถิติย้อนหลัง) — DXY สูง = บาทอ่อน = หนุนทองไทย → กดดันขายต่ำ; DXY ต่ำ = กดดันขายสูง",
              formula: "= map(ช่วง DXY → คะแนน): <80→70 · 80–90→58 · 90–100→45 · 100–110→18 · >110→15" },
            { name: "ฤดูกาล", weight: "5%", cur: signal?.seasonality, desc: "รูปแบบราคาตามเดือนในอดีต (เช่น มิ.ย. มักอ่อนแรง) · น้ำหนักน้อยเพราะขึ้นกับสภาวะตลาด ไม่แน่นอน",
              formula: "= map(ผลตอบแทนเฉลี่ยรายเดือนย้อนหลัง) → เดือนอ่อนแรง = คะแนนสูง" },
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
          ผลทดสอบย้อนหลัง — กรอบ 12 เดือน (ปี 2010–2026)
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.6 }}>
          “จับยอด” = สัดส่วนของช่วงราคาที่ขายได้จริงในแต่ละกรอบเวลา ตลอด 16 ปีที่ทองเป็นขาขึ้น การ{" "}
          <b style={{ color: "var(--text)" }}>ถือไว้</b> ให้ผลดีที่สุด — และคะแนนรวมของเราเป็นกฎ <i>เชิงรุก</i> ที่ดีที่สุด
          เอาชนะการทยอยขายแบบ DCA ค่าเฉลี่ยดูเข้าข้างการถือ เพราะเทรนด์แทบไม่เคยพลิก คะแนนจะมีค่าจริงตอนที่เทรนด์กลับตัว
        </p>
        <div className="muted mono" style={{ fontSize: 11, marginTop: 12 }}>
          OOS = ทดสอบช่วงนอกตัวอย่าง (window เริ่มปี 2020) · กลยุทธ์ที่ทำได้ดีจริงควรชนะทั้งช่วงเต็มและ OOS
        </div>
        <div style={{ marginTop: 10 }}>
          <BacktestTable runs={runs} />
        </div>
        {dd.dropPct < 0 && (
          <div className="panel" style={{ background: "var(--panel2)", padding: 16, marginTop: 16 }}>
            <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4 }}>
              ข้อยกเว้น — ปี 2013
            </div>
            <p style={{ fontSize: 14, marginTop: 6, lineHeight: 1.6 }}>
              ตั้งแต่ {bangkokDate(dd.peakDate).split(" ").slice(0, 3).join(" ")} ราคาทองสากลร่วงลง{" "}
              <b className="mono" style={{ color: "var(--red)" }}>{pct(dd.dropPct)}</b> มาที่{" "}
              {bangkokDate(dd.troughDate).split(" ").slice(0, 3).join(" ")} ({thb(dd.peak)} → {thb(dd.trough)} /บาททอง)
              นี่คือการกลับตัวของเทรนด์ที่เครื่องมือนี้มีไว้เพื่อจับ — ซึ่งค่าเฉลี่ยในตารางด้านบนมองข้ามไป
            </p>
          </div>
        )}
      </section>

      {/* Dollar Index regime */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          ดัชนีดอลลาร์ (DXY) → ผลตอบแทนทอง 12 เดือนข้างหน้า
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.55 }}>
          สถิติย้อนหลัง (ทองคำบาท 2006–2026): แบ่งตามระดับ DXY แล้วดูผลตอบแทนเฉลี่ย / ขาดทุนเฉลี่ย / ผลตอบแทนต่อ max drawdown ใน
          12 เดือนถัดมา · ระดับปัจจุบันถูกนำมารวมในคะแนน (ส่วน “ดอลลาร์” 12%) · ⚠️ ช่วง &lt;80 และ &gt;110 ตัวอย่างน้อย เชื่อถือได้จำกัด
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
        ใช้เพื่อประกอบการตัดสินใจ ไม่ใช่คำแนะนำการลงทุน · สัญญาณคำนวณจากข้อมูลในอดีตแบบ in-sample (ช่วงเวลาทับซ้อน
        ความเชื่อมั่นจึงกว้าง) · ผลในอดีตไม่รับประกันอนาคต · ฐานคะแนน: ราคาทองสากล (LBMA × USD/THB จาก ECB) · ราคารับซื้อจริง:
        สมาคมค้าทองคำแห่งประเทศไทย · ข่าว: Google News · ปฏิทิน: ForexFactory
      </footer>
    </main>
  );
}
