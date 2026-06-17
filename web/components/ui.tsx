import type { BacktestRun, Verdict } from "@/lib/types";
import { pct } from "@/lib/format";

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
  if (score >= 55) return "var(--red)";
  if (score >= 45) return "var(--orange)";
  if (score >= 35) return "var(--amber)";
  return "var(--green)";
}

export function ScoreGauge({ score }: { score: number }) {
  return (
    <div>
      <div className="mono" style={{ fontSize: 56, lineHeight: 1, color: zoneColor(score) }}>
        {score.toFixed(0)}
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
        {[35, 45, 55].map((t) => (
          <div key={t} style={{ position: "absolute", left: `${t}%`, top: -3, bottom: -3, width: 1, background: "var(--border)" }} />
        ))}
      </div>
      <div className="muted mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginTop: 6 }}>
        <span>0 ถือ</span>
        <span>ลด 35</span>
        <span>บางส่วน 45</span>
        <span>ขาย 55</span>
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
            {b.value.toFixed(0)}
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
