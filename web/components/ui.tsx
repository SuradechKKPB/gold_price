import type { BacktestRun, PlanStatus, RegimeStats, TrailState, Verdict } from "@/lib/types";
import type { Indicator, KeyLevel, State } from "@/lib/ta";
import type { TruthPost } from "@/lib/trump";
import { type DxyBand, bandOf } from "@/lib/dxy";
import { newsDate, pct, thb } from "@/lib/format";

const VERDICT: Record<Verdict, { label: string; color: string }> = {
  weak: { label: "ราคาอ่อนตัว · ยังไม่ควรขาย", color: "var(--blue)" },
  neutral: { label: "ปกติ", color: "var(--muted)" },
  rich: { label: "โซนแพง", color: "var(--amber)" },
  very_rich: { label: "โซนแพงมาก · จังหวะขาย", color: "var(--orange)" },
  // v3 names: only seen before the ETL's first v4 run rewrites signals_daily.
  hold: { label: "ถือไว้", color: "var(--green)" },
  trim: { label: "ลดพอร์ตเล็กน้อย", color: "var(--amber)" },
  sell_tranche: { label: "ขายบางส่วน", color: "var(--orange)" },
  sell: { label: "ขายออก", color: "var(--red)" },
};

export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const v = VERDICT[verdict] ?? VERDICT.neutral;
  return (
    <span
      className="mono"
      style={{
        color: v.color,
        border: `1px solid ${v.color}`,
        borderRadius: 999,
        padding: "4px 12px",
        fontSize: 13,
        letterSpacing: 0.5,
      }}
    >
      {v.label}
    </span>
  );
}

// DISPLAY-ONLY mirrors of etl/signals.py RICH and VERY_RICH. They place the two ticks;
// the verdict itself (and its colour) comes from the DB, so a drift here mislabels a tick
// but cannot change what the page says to do.
const RICH_LINE = 80;
const VERY_RICH_LINE = 90;

export function ScoreGauge({ score, verdict }: { score: number; verdict: Verdict }) {
  const color = (VERDICT[verdict] ?? VERDICT.neutral).color;
  return (
    <div>
      <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4, marginBottom: 6 }}>
        คะแนนจังหวะขาย (percentile ความแข็งของราคา รอบ 3 ปี)
      </div>
      <div className="mono" style={{ fontSize: 56, lineHeight: 1, color }}>
        {(score ?? 0).toFixed(0)}
        <span className="muted" style={{ fontSize: 18 }}>
          /100
        </span>
      </div>
      <div style={{ position: "relative", height: 8, background: "var(--panel2)", borderRadius: 6, marginTop: 16 }}>
        <div style={{ position: "absolute", inset: 0, width: `${score}%`, background: color, borderRadius: 6 }} />
        {[RICH_LINE, VERY_RICH_LINE].map((t) => (
          <div key={t} style={{ position: "absolute", left: `${t}%`, top: -3, bottom: -3, width: 1, background: "var(--border)" }} />
        ))}
      </div>
      <div className="muted mono" style={{ position: "relative", height: 14, fontSize: 11, marginTop: 6 }}>
        <span style={{ position: "absolute", left: 0 }}>0 อ่อน</span>
        <span style={{ position: "absolute", left: `${RICH_LINE}%`, transform: "translateX(-100%)", paddingRight: 4 }}>แพง {RICH_LINE}</span>
        <span style={{ position: "absolute", left: `${VERY_RICH_LINE}%`, transform: "translateX(-50%)" }}>{VERY_RICH_LINE}</span>
        <span style={{ position: "absolute", right: 0 }}>100</span>
      </div>
    </div>
  );
}

/** Where price stands against the 40-bar high, and how far that is from the brake band.
 *
 *  In v4 a drop from the high no longer says "sell". It says "not today": a tranche that
 *  falls due while price is past the band (and the score is below 50) may wait. The band is
 *  k x one-month sigma, published by etl.compute as `brake_dd`, so nothing here copies a
 *  threshold. The v3 3%/8% band was hand-copied into this file and went stale whenever the
 *  Python moved. */
export function TrailStop({ trail, price, live }: { trail: TrailState; price: number; live: boolean }) {
  // Measure the distance against the price actually shown in the hero. trail.ddFromHigh is
  // derived from the last stored DAILY CLOSE, so rendering it beside a real-time figure puts
  // two clocks in one panel: on 2026-08-31 the stored dd read 4.2% while the live price
  // gave 2.90%, on opposite sides of the then 3% line. The HIGH still comes from the DB,
  // since a 40-bar rolling max barely moves intraday. Mirrors trailFrom() in
  // worker/src/index.js. A live price above the stored high IS a new high: raise it, report 0.
  const high = live ? Math.max(trail.recentHigh, price) : trail.recentHigh;
  const dd = live ? (high - price) / high : trail.ddFromHigh;
  const band = trail.brakeDd;
  const span = Math.max(0.12, (band ?? 0) * 2); // the bar charts twice the band, at least 12%
  const zone =
    band == null
      ? { color: "var(--muted)", label: "" }
      : dd < band / 2
        ? { color: "var(--green)", label: "ใกล้ยอด" }
        : dd < band
          ? { color: "var(--amber)", label: "ย่อตัว ยังไม่ถึงเส้นเบรก" }
          : { color: "var(--blue)", label: "เลยเส้นเบรก" };
  const at = (x: number) => `${Math.min(x / span, 1) * 100}%`;

  return (
    <div>
      <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4 }}>
        ต่ำกว่ายอด 40 วัน (ฐานของเบรก)
      </div>
      <div className="mono" style={{ fontSize: 32, lineHeight: 1.1, marginTop: 6, color: zone.color }}>
        −{pct(dd, 1)}
        {zone.label && <span className="muted" style={{ fontSize: 14 }}> · {zone.label}</span>}
      </div>
      <div className="muted mono" style={{ fontSize: 12, marginTop: 4 }}>
        ยอด {thb(high)} · ตอนนี้ {thb(price)} /บาททอง{live ? "" : " · ณ ราคาปิด"}
      </div>

      <div style={{ position: "relative", height: 8, background: "var(--panel2)", borderRadius: 6, marginTop: 14 }}>
        <div style={{ position: "absolute", inset: 0, width: at(dd), background: zone.color, borderRadius: 6 }} />
        {band != null && (
          <div style={{ position: "absolute", left: at(band), top: -3, bottom: -3, width: 1, background: "var(--text)" }} />
        )}
      </div>
      <div className="muted mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginTop: 6 }}>
        <span>ยอด 0%</span>
        {band != null && <span>เบรก {pct(band, 1)} (1σ ของ 1 เดือน)</span>}
        <span>{pct(span)}+</span>
      </div>
      {band != null && (
        <div className="muted" style={{ fontSize: 12, marginTop: 8, lineHeight: 1.5 }}>
          เบรกทำงานเมื่อราคาเลยเส้นนี้ <i>และ</i> คะแนนต่ำกว่า 50 · ไม้ที่ครบกำหนดในวันแบบนี้รอได้ไม่เกิน 10 วันทำการ
        </div>
      )}
    </div>
  );
}

const ACTION: Record<PlanStatus["action"], { label: string; color: string }> = {
  sell: { label: "ขายไม้นี้ได้รอบนี้", color: "var(--orange)" },
  wait: { label: "รอก่อน · ราคาอ่อนตัว", color: "var(--blue)" },
  hold: { label: "ถือรอ", color: "var(--text)" },
  done: { label: "ครบตามแผนแล้ว", color: "var(--green)" },
  ended: { label: "จบแผนแล้ว", color: "var(--muted)" },
};

const REASON: Record<string, string> = {
  rich: "ราคาอยู่โซนแพงมาก จึงขายไม้ของช่วงนี้ก่อนกำหนดได้",
  due: "ครบกำหนดไม้นี้แล้ว และไม่ใช่วันราคาอ่อน",
  deadline: "ใกล้เส้นตาย ต้องขายตามแผนไม่ว่าราคาเป็นอย่างไร",
  brake: "ครบกำหนดแล้วแต่ราคาอ่อนตัว รอได้ถึงวันที่ระบุ",
  ahead: "ยังไม่ถึงกำหนด จะขายก่อนก็ต่อเมื่อเข้าโซนแพงมาก",
  plan_met: "ขายครบตามเป้าแล้ว ที่เหลือถือต่อหรือขายเพิ่มได้ตามสะดวก",
  past_deadline: "เลยวันสิ้นสุดของแผนแล้ว",
};

const shortDate = (iso: string) =>
  new Date(`${iso}T00:00:00+07:00`).toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok", day: "numeric", month: "short" });

/** The sell plan (etl/plan.py): pace is the seller's, the score only picks the day. */
export function PlanPanel({ plan }: { plan: PlanStatus }) {
  const a = ACTION[plan.action] ?? ACTION.hold;
  const done = Math.min(plan.sold_grams / plan.sell_grams, 1);
  const schedule = plan.schedule ?? [];
  return (
    <div>
      <div className="muted" style={{ fontSize: 12, letterSpacing: 0.4 }}>
        แผนขาย · อย่างน้อย {plan.sell_grams.toLocaleString()} ก. จาก {plan.holding_grams.toLocaleString()} ก. ภายใน {shortDate(plan.deadline)}
      </div>
      <div className="serif" style={{ fontSize: 26, marginTop: 6, color: a.color }}>
        {a.label}
        {plan.tranche && plan.action !== "done" && plan.action !== "ended" ? (
          <span className="muted mono" style={{ fontSize: 14 }}> · ไม้ {plan.tranche}/{plan.n_tranches}</span>
        ) : null}
      </div>
      <div className="muted" style={{ fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
        {REASON[plan.reason] ?? plan.reason}
        {plan.action === "wait" && plan.wait_until ? ` (${shortDate(plan.wait_until)})` : ""}
        {plan.action === "sell" && (plan.count ?? 1) > 1 ? ` · รอบนี้ขาย ${plan.count} ไม้` : ""}
        {plan.action === "ended" && (plan.remaining_grams ?? 0) > 0 ? ` · ยังขาดอีก ${plan.remaining_grams?.toLocaleString()} ก.` : ""}
      </div>

      <div style={{ position: "relative", height: 8, background: "var(--panel2)", borderRadius: 6, marginTop: 14 }}>
        <div style={{ position: "absolute", inset: 0, width: `${done * 100}%`, background: "var(--gold)", borderRadius: 6 }} />
        {Array.from({ length: plan.n_tranches - 1 }, (_, i) => (
          <div key={i} style={{ position: "absolute", left: `${((i + 1) / plan.n_tranches) * 100}%`, top: 0, bottom: 0, width: 2, background: "var(--panel)" }} />
        ))}
      </div>
      <div className="muted mono" style={{ fontSize: 12, marginTop: 6 }}>
        ขายแล้ว {plan.sold_grams.toLocaleString()} / {plan.sell_grams.toLocaleString()} ก.
        {plan.next_grams ? ` · ไม้ถัดไป ${plan.next_grams.toLocaleString()} ก.` : ""}
      </div>

      <div className="mono" style={{ display: "grid", gridTemplateColumns: `repeat(${schedule.length || 1}, 1fr)`, gap: 6, marginTop: 14, fontSize: 12 }}>
        {schedule.map((d, i) => {
          const sold = i < plan.tranches_sold;
          const next = i === plan.tranches_sold && plan.action !== "done";
          return (
            <div key={i} style={{ borderTop: `2px solid ${sold ? "var(--gold)" : next ? a.color : "var(--border)"}`, paddingTop: 6 }}>
              <div className="muted" style={{ fontSize: 11 }}>ไม้ {i + 1}</div>
              <div style={{ color: sold ? "var(--muted)" : "var(--text)" }}>{sold ? "ขายแล้ว" : d ? `ภายใน ${shortDate(d)}` : "—"}</div>
            </div>
          );
        })}
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 10 }}>
        ข้อมูล ณ {plan.as_of} · บันทึกการขาย: <span className="mono">python -m etl.plan sold 100 --price 71650</span>
      </div>
    </div>
  );
}

export function BucketBars({ buckets }: { buckets: { label: string; value: number }[] }) {
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {buckets.map((b) => (
        <div key={b.label} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="muted" style={{ width: 110, fontSize: 13 }}>
            {b.label}
          </span>
          <div style={{ flex: 1, height: 6, background: "var(--panel2)", borderRadius: 4 }}>
            <div style={{ width: `${b.value}%`, height: "100%", background: "var(--gold)", borderRadius: 4 }} />
          </div>
          <span className="mono" style={{ width: 32, textAlign: "right", fontSize: 13 }}>
            {(b.value ?? 0).toFixed(0)}
          </span>
        </div>
      ))}
    </div>
  );
}

const STRATEGY: Record<string, string> = {
  plan_signal: "แผน + คะแนนเลือกวัน (ใช้จริง)",
  plan_slot_mid: "แผน · ขายกลางช่วง (≈ สุ่มวัน)",
  plan_slot_end: "แผน · ขายปลายช่วง (ไม่ใช้คะแนน)",
  sell_all_at_end: "ถือแล้วขายทีเดียวตอนท้าย",
};
const REGIMES = ["2006-11 bull", "2011-18 bear", "2019-26 bull"];

function triple(stats: Record<string, RegimeStats> | undefined | null, key: "edge_pct" | "skill_pct"): string {
  if (!stats) return "—";
  return REGIMES.map((r) => {
    const v = stats[r]?.[key];
    return v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
  }).join(" · ");
}

/** v4 harness (etl/backtest.py): drift-neutral day skill beside the campaign edge and the
 *  average sale day, per regime, so selling later in a bull market cannot pass for skill. */
export function BacktestTable({ runs }: { runs: BacktestRun[] }) {
  const v4 = runs.filter((r) => r.params?.by_regime && STRATEGY[r.strategy]);
  if (!v4.length) {
    return (
      <div className="muted" style={{ fontSize: 13 }}>
        ยังไม่มีผลทดสอบของคะแนน v4 · รัน <span className="mono">python -m etl.backtest --write</span>
      </div>
    );
  }
  const order = Object.keys(STRATEGY);
  v4.sort((a, b) => order.indexOf(a.strategy) - order.indexOf(b.strategy));
  const signal = v4.find((r) => r.strategy === "plan_signal");
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="mono" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 560 }}>
        <thead>
          <tr className="muted" style={{ textAlign: "right" }}>
            <th style={{ textAlign: "left", paddingBottom: 8 }}>กลยุทธ์</th>
            <th style={{ paddingBottom: 8 }}>ทักษะเลือกวัน %</th>
            <th style={{ paddingBottom: 8 }}>เทียบขายปลายช่วง %</th>
            <th style={{ paddingBottom: 8 }}>วันขายเฉลี่ย</th>
          </tr>
          <tr className="muted" style={{ textAlign: "right", fontSize: 11 }}>
            <th />
            <th style={{ paddingBottom: 8, fontWeight: 400 }}>06–11 · 11–18 · 19–26</th>
            <th style={{ paddingBottom: 8, fontWeight: 400 }}>06–11 · 11–18 · 19–26</th>
            <th style={{ paddingBottom: 8, fontWeight: 400 }}>จาก {v4[0].horizon_days}</th>
          </tr>
        </thead>
        <tbody>
          {v4.map((r) => (
            <tr key={r.strategy} style={{ borderTop: "1px solid var(--border)", color: r.strategy === "plan_signal" ? "var(--gold)" : "var(--text)" }}>
              <td style={{ textAlign: "left", padding: "7px 0" }}>{STRATEGY[r.strategy]}</td>
              <td style={{ textAlign: "right" }}>{triple(r.params?.by_regime, "skill_pct")}</td>
              <td style={{ textAlign: "right" }}>{triple(r.params?.by_regime, "edge_pct")}</td>
              <td style={{ textAlign: "right" }}>{r.params?.by_regime?.all?.avg_day?.toFixed(0) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {signal?.params?.vs_random_day && (
        <div className="muted mono" style={{ fontSize: 12, marginTop: 10 }}>
          แผน + คะแนน เทียบสุ่มวันในช่วงเดียวกัน: {triple(signal.params.vs_random_day, "edge_pct")} % · ตัวอย่างอิสระ ≈ {signal.params.n_eff} ชุด
        </div>
      )}
    </div>
  );
}

const STATE: Record<State, { color: string; label: string }> = {
  bear: { color: "var(--red)", label: "ขาลง / กดดันขาย" },
  warn: { color: "var(--orange)", label: "เตือน" },
  neutral: { color: "var(--muted)", label: "กลาง" },
  bull: { color: "var(--green)", label: "ขาขึ้น" },
};

export function IndicatorsTable({ indicators }: { indicators: Indicator[] }) {
  return (
    <div style={{ display: "grid", gap: 0 }}>
      {indicators.map((x) => (
        <div
          key={x.name}
          style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, borderTop: "1px solid var(--border)", padding: "10px 0" }}
        >
          <div>
            <div style={{ fontSize: 14 }}>{x.name}</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{x.note}</div>
          </div>
          <div style={{ textAlign: "right", flexShrink: 0 }}>
            <div className="mono" style={{ fontSize: 14 }}>{x.value}</div>
            <span className="mono" style={{ fontSize: 11, color: STATE[x.state].color }}>{STATE[x.state].label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function KeyLevels({ levels }: { levels: KeyLevel[] }) {
  return (
    <div style={{ display: "grid", gap: 12 }}>
      {levels.map((lv) => (
        <div key={lv.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
          <span style={{ fontSize: 13 }}>{lv.name}</span>
          <span className="mono" style={{ fontSize: 13, flexShrink: 0 }}>
            {thb(lv.level)}{" "}
            <span style={{ color: Math.abs(lv.distPct) < 3 ? "var(--orange)" : "var(--muted)" }}>
              ({lv.distPct >= 0 ? "+" : ""}{(lv.distPct ?? 0).toFixed(1)}%)
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

export function DxyPanel({ table, current }: { table: DxyBand[]; current: number | null }) {
  const cur = current != null ? bandOf(current) : null;
  return (
    <div>
      {current != null && (
        <div className="mono" style={{ fontSize: 13, marginBottom: 14 }}>
          DXY ตอนนี้{" "}
          <span style={{ color: "var(--gold)", fontSize: 20 }}>{current.toFixed(1)}</span> · อยู่ในช่วง <b>{cur}</b>
        </div>
      )}
      <table className="mono" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr className="muted" style={{ textAlign: "right" }}>
            <th style={{ textAlign: "left", paddingBottom: 8 }}>ช่วง DXY</th>
            <th style={{ paddingBottom: 8 }}>ผลตอบแทน 12ด.</th>
            <th style={{ paddingBottom: 8 }}>ขาดทุนเฉลี่ย</th>
            <th style={{ paddingBottom: 8 }}>ret/maxDD</th>
            <th style={{ paddingBottom: 8 }}>%บวก</th>
            <th style={{ paddingBottom: 8 }}>n</th>
          </tr>
        </thead>
        <tbody>
          {table.map((r) => {
            const here = r.band === cur;
            return (
              <tr key={r.band} style={{ borderTop: "1px solid var(--border)", background: here ? "var(--panel2)" : "transparent" }}>
                <td style={{ textAlign: "left", padding: "7px 6px", color: here ? "var(--gold)" : "var(--text)" }}>
                  {r.band}{here ? " ←" : ""}
                </td>
                <td style={{ textAlign: "right" }}>+{r.avgRet}%</td>
                <td style={{ textAlign: "right", color: "var(--red)" }}>{r.avgLoss}%</td>
                <td style={{ textAlign: "right" }}>{r.retDD ?? "—"}</td>
                <td style={{ textAlign: "right" }}>{r.posPct}%</td>
                <td style={{ textAlign: "right", color: "var(--muted)" }}>{r.n}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function TruthFeed({ posts }: { posts: TruthPost[] }) {
  if (!posts.length) {
    return <div className="muted" style={{ fontSize: 13 }}>ไม่มีโพสต์ที่เกี่ยวกับตลาดในช่วงนี้</div>;
  }
  return (
    <div style={{ display: "grid", gap: 14 }}>
      {posts.map((p, i) => (
        <a key={i} href={p.url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--text)", textDecoration: "none", display: "block" }}>
          <div style={{ fontSize: 13.5, lineHeight: 1.5 }}>{p.text}</div>
          <div className="muted mono" style={{ fontSize: 11, marginTop: 3 }}>
            Truth Social{p.date ? ` · ${newsDate(p.date)}` : ""}
          </div>
        </a>
      ))}
    </div>
  );
}
