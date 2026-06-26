import type { BacktestRun, Verdict } from "@/lib/types";
import type { Indicator, KeyLevel, State } from "@/lib/ta";
import type { TruthPost } from "@/lib/trump";
import { type DxyBand, bandOf } from "@/lib/dxy";
import { newsDate, pct, thb } from "@/lib/format";

const VERDICT: Record<Verdict, { label: string; color: string }> = {
  hold: { label: "ถือไว้", color: "var(--green)" },
  trim: { label: "ลดพอร์ตเล็กน้อย", color: "var(--amber)" },
  sell_tranche: { label: "ขายบางส่วน", color: "var(--orange)" },
  sell: { label: "ขายออก", color: "var(--red)" },
};

export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const v = VERDICT[verdict] ?? VERDICT.hold;
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

function zoneColor(score: number): string {
  if (score >= 50) return "var(--red)";
  if (score >= 42) return "var(--orange)";
  if (score >= 33) return "var(--amber)";
  return "var(--green)";
}

export function ScoreGauge({ score }: { score: number }) {
  return (
    <div>
      <div className="mono" style={{ fontSize: 56, lineHeight: 1, color: zoneColor(score) }}>
        {(score ?? 0).toFixed(0)}
        <span className="muted" style={{ fontSize: 18 }}>
          /100
        </span>
      </div>
      <div style={{ position: "relative", height: 8, background: "var(--panel2)", borderRadius: 6, marginTop: 16 }}>
        <div
          style={{
            position: "absolute",
            inset: 0,
            width: `${score}%`,
            background: zoneColor(score),
            borderRadius: 6,
          }}
        />
        {[33, 42, 50].map((t) => (
          <div key={t} style={{ position: "absolute", left: `${t}%`, top: -3, bottom: -3, width: 1, background: "var(--border)" }} />
        ))}
      </div>
      <div className="muted mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginTop: 6 }}>
        <span>0 ถือ</span>
        <span>ลด 33</span>
        <span>บางส่วน 42</span>
        <span>ขาย 50</span>
        <span>100</span>
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

function prettyStrategy(s: string): string {
  if (s === "window_end") return "ถือจนครบกรอบ";
  if (s === "dca_out") return "ทยอยขายเฉลี่ย (DCA)";
  if (s === "random_day") return "สุ่มวันขาย";
  const trail = s.match(/^trail_(aao|ladder)_(\d+)$/);
  if (trail) return `Trailing ${trail[2]}% ${trail[1] === "aao" ? "(ขายทีเดียว)" : "(ขั้นบันได)"}`;
  const score = s.match(/^score_ge_(\d+)$/);
  if (score) return `คะแนน ≥ ${score[1]}`;
  return s;
}

export function BacktestTable({ runs }: { runs: BacktestRun[] }) {
  return (
    <table className="mono" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
      <thead>
        <tr className="muted" style={{ textAlign: "right" }}>
          <th style={{ textAlign: "left", paddingBottom: 8 }}>กลยุทธ์</th>
          <th style={{ paddingBottom: 8 }}>จับยอด</th>
          <th style={{ paddingBottom: 8 }}>OOS</th>
          <th style={{ paddingBottom: 8 }}>พลาด/บาททอง</th>
          <th style={{ paddingBottom: 8 }}>ชนะ DCA</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.strategy} style={{ borderTop: "1px solid var(--border)" }}>
            <td style={{ textAlign: "left", padding: "7px 0" }}>{prettyStrategy(r.strategy)}</td>
            <td style={{ textAlign: "right" }}>{pct(r.median_capture_pct)}</td>
            <td style={{ textAlign: "right" }}>
              {r.params?.oos_capture_pct != null ? pct(r.params.oos_capture_pct) : "—"}
            </td>
            <td style={{ textAlign: "right" }}>{r.median_regret_thb.toLocaleString()}</td>
            <td style={{ textAlign: "right" }}>
              {r.win_rate_vs_dca === null ? "—" : pct(r.win_rate_vs_dca)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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
