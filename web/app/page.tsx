import PriceChart from "@/components/PriceChart";
import { BacktestTable, BucketBars, ScoreGauge, VerdictChip } from "@/components/ui";
import { drawdown, sma } from "@/lib/indicators";
import { bahtWeight, bangkokDate, calDate, newsDate, num, pct, thb } from "@/lib/format";
import { fetchCalendar, fetchNews } from "@/lib/news";
import { getBacktest, getLatestSignal, getLatestTick, getPriceHistory } from "@/lib/queries";

export const revalidate = 1800;

const SIGNAL_LABELS: Record<string, string> = {
  below_chandelier: "หลุด Chandelier stop รายสัปดาห์",
  below_donchian_10w: "หลุดจุดต่ำสุด 10 สัปดาห์",
  below_donchian_20w: "หลุดจุดต่ำสุด 20 สัปดาห์",
  death_cross: "เดธครอส 50/200",
  below_200dma: "หลุดเส้นค่าเฉลี่ย 200 วัน",
  rsi_weekly_gt70: "RSI รายสัปดาห์ > 70",
  stretch_gt18pct: "สูงกว่าเส้น 200 วัน > 18%",
  pctb_gt1: "เหนือกรอบบอลลินเจอร์บน",
  macd_bearish: "MACD รายสัปดาห์เป็นขาลง",
};

export default async function Page() {
  const grams = Number(process.env.GOLD_GRAMS ?? 700);
  const bw = bahtWeight(grams);

  const [signal, tick, prices, runs, news, events] = await Promise.all([
    getLatestSignal(),
    getLatestTick(),
    getPriceHistory(),
    getBacktest(252),
    fetchNews(),
    fetchCalendar(),
  ]);

  const buyIn = tick?.bar_buy ?? prices.at(-1)?.bar_buy_close ?? 0;
  const holdingValue = bw * buyIn;

  const priceSeries = prices.map((r) => ({ time: r.trade_date, value: r.bar_buy_close }));
  const ma200 = sma(prices, 200);
  const dd = drawdown(prices, "2011-01-01", "2014-12-31");

  const buckets = signal
    ? [
        { label: "เบรกเทรนด์", value: signal.trend_break },
        { label: "ซื้อมากเกินไป", value: signal.overbought },
        { label: "โมเมนตัม", value: signal.momentum },
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
            {grams} กรัม ({num(bw)} บาททอง) ที่ราคารับซื้อวันนี้
          </div>
          <div className="mono serif" style={{ fontSize: 44, marginTop: 8, color: "var(--gold)" }}>
            ฿{thb(holdingValue)}
          </div>
          <div className="muted mono" style={{ fontSize: 13, marginTop: 6 }}>
            รับซื้อ {thb(buyIn)} /บาททอง{tick ? ` · สปอต $${num(tick.gold_spot_usd)} · USDTHB ${num(tick.baht_per_usd)}` : ""}
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
          <div style={{ marginTop: 20 }}>
            <BucketBars buckets={buckets} />
          </div>
        </div>
      </section>

      {/* Price chart */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
            ราคารับซื้อทองคำแท่ง ปี 2006–ปัจจุบัน
          </h2>
          <span className="muted mono" style={{ fontSize: 12 }}>
            เส้นทอง = ค่าเฉลี่ย 200 วัน
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
        <div style={{ marginTop: 16 }}>
          <BacktestTable runs={runs} />
        </div>
        {dd.dropPct < 0 && (
          <div className="panel" style={{ background: "var(--panel2)", padding: 16, marginTop: 16 }}>
            <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4 }}>
              ข้อยกเว้น — ปี 2013
            </div>
            <p style={{ fontSize: 14, marginTop: 6, lineHeight: 1.6 }}>
              ตั้งแต่ {bangkokDate(dd.peakDate).split(" ").slice(0, 3).join(" ")} ราคารับซื้อร่วงลง{" "}
              <b className="mono" style={{ color: "var(--red)" }}>{pct(dd.dropPct)}</b> มาที่{" "}
              {bangkokDate(dd.troughDate).split(" ").slice(0, 3).join(" ")} ({thb(dd.peak)} → {thb(dd.trough)} /บาททอง)
              นี่คือการกลับตัวของเทรนด์ที่เครื่องมือนี้มีไว้เพื่อจับ — ซึ่งค่าเฉลี่ยในตารางด้านบนมองข้ามไป
            </p>
          </div>
        )}
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

      <footer className="muted" style={{ fontSize: 12, marginTop: 28, lineHeight: 1.6 }}>
        ใช้เพื่อประกอบการตัดสินใจ ไม่ใช่คำแนะนำการลงทุน · สัญญาณคำนวณจากข้อมูลในอดีตแบบ in-sample (ช่วงเวลาทับซ้อน
        ความเชื่อมั่นจึงกว้าง) · ผลในอดีตไม่รับประกันอนาคต · ที่มาราคา: สมาคมค้าทองคำแห่งประเทศไทย · ข่าว: Google News ·
        ปฏิทิน: ForexFactory
      </footer>
    </main>
  );
}
