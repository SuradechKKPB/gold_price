import type { PriceRow } from "./types";

// Additional daily technical indicators for the dashboard, computed server-side from
// the daily-updated price history. These are DISPLAY context (the calibrated 0-100
// score lives in the Python engine). Buy-in basis throughout (bar_sell − spread).
const BAR_SPREAD = 200;

export type State = "bear" | "warn" | "neutral" | "bull";
export interface Indicator {
  name: string;
  value: string;
  state: State;
  note: string;
}
export interface KeyLevel {
  name: string;
  level: number;
  distPct: number; // % of current price away (− = price is above the level)
}

function sma(a: number[], n: number, i: number): number {
  if (i < n - 1) return NaN;
  let s = 0;
  for (let k = i - n + 1; k <= i; k++) s += a[k];
  return s / n;
}

function rsiWilder(c: number[], period = 14): number {
  if (c.length <= period) return NaN;
  let g = 0, l = 0;
  for (let i = 1; i <= period; i++) {
    const d = c[i] - c[i - 1];
    if (d >= 0) g += d; else l -= d;
  }
  let ag = g / period, al = l / period;
  for (let i = period + 1; i < c.length; i++) {
    const d = c[i] - c[i - 1];
    ag = (ag * (period - 1) + Math.max(d, 0)) / period;
    al = (al * (period - 1) + Math.max(-d, 0)) / period;
  }
  if (al === 0) return 100;
  return 100 - 100 / (1 + ag / al);
}

function roc(c: number[], n: number): number {
  const i = c.length - 1;
  return i - n >= 0 && c[i - n] ? (c[i] / c[i - n] - 1) * 100 : NaN;
}

/** Coppock Curve = WMA10(ROC220 + ROC294) — long-horizon momentum-turn indicator. */
function coppock(c: number[]): { value: number; prev: number } {
  const N = c.length;
  const s: number[] = new Array(N).fill(NaN);
  for (let i = 294; i < N; i++) s[i] = (c[i] / c[i - 220] - 1) * 100 + (c[i] / c[i - 294] - 1) * 100;
  const cop: number[] = new Array(N).fill(NaN);
  for (let i = 303; i < N; i++) {
    let acc = 0, ok = true;
    for (let k = 0; k < 10; k++) {
      const v = s[i - 9 + k];
      if (Number.isNaN(v)) { ok = false; break; }
      acc += v * (k + 1);
    }
    if (ok) cop[i] = acc / 55;
  }
  return { value: cop[N - 1], prev: cop[N - 2] };
}

/** ADX / DMI (Wilder, 14) from OHLC. */
function adxDmi(h: number[], l: number[], c: number[], period = 14): { adx: number; pdi: number; mdi: number } {
  const n = c.length;
  const tr: number[] = [], pdm: number[] = [], mdm: number[] = [];
  for (let i = 1; i < n; i++) {
    const up = h[i] - h[i - 1], dn = l[i - 1] - l[i];
    pdm.push(up > dn && up > 0 ? up : 0);
    mdm.push(dn > up && dn > 0 ? dn : 0);
    tr.push(Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])));
  }
  const wilder = (a: number[]): number[] => {
    const o: number[] = [];
    let s = 0;
    for (let i = 0; i < a.length; i++) {
      if (i < period) { s += a[i]; o[i] = i === period - 1 ? s : NaN; }
      else o[i] = o[i - 1] - o[i - 1] / period + a[i];
    }
    return o;
  };
  const str = wilder(tr), sp = wilder(pdm), sm = wilder(mdm);
  const dx: number[] = [];
  let pdiLast = NaN, mdiLast = NaN;
  for (let i = 0; i < str.length; i++) {
    if (Number.isNaN(str[i]) || str[i] === 0) { dx[i] = NaN; continue; }
    const pdi = (100 * sp[i]) / str[i], mdi = (100 * sm[i]) / str[i];
    pdiLast = pdi; mdiLast = mdi;
    const sum = pdi + mdi;
    dx[i] = sum === 0 ? 0 : (100 * Math.abs(pdi - mdi)) / sum;
  }
  let adx = NaN, cnt = 0, acc = 0, started = false;
  for (let i = 0; i < dx.length; i++) {
    if (Number.isNaN(dx[i])) continue;
    if (!started) { acc += dx[i]; if (++cnt === period) { adx = acc / period; started = true; } }
    else adx = (adx * (period - 1) + dx[i]) / period;
  }
  return { adx, pdi: pdiLast, mdi: mdiLast };
}

function fmt(n: number, d = 0): string {
  return Number.isNaN(n) ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
}
const sign = (n: number, d = 0) => (n >= 0 ? "+" : "") + fmt(n, d);

export function computeTA(rows: PriceRow[]): { indicators: Indicator[]; levels: KeyLevel[] } {
  const c = rows.map((r) => r.bar_buy_close);
  const h = rows.map((r) => r.bar_sell_high - BAR_SPREAD);
  const l = rows.map((r) => r.bar_sell_low - BAR_SPREAD);
  const i = c.length - 1;
  const close = c[i];
  const ma200 = sma(c, 200, i);

  const cop = coppock(c);
  const r3 = roc(c, 63), r6 = roc(c, 126), r12 = roc(c, 252);
  const negCount = [r3, r6, r12].filter((x) => x < 0).length;
  const dm = adxDmi(h, l, c, 14);
  const rsi = rsiWilder(c, 14);
  const mayer = close / ma200;
  // drawdown from all-time-high
  let peak = -Infinity, peakIdx = 0;
  for (let k = 0; k < c.length; k++) if (c[k] > peak) { peak = c[k]; peakIdx = k; }
  const dd = (close / peak - 1) * 100;

  const indicators: Indicator[] = [
    {
      name: "Coppock Curve",
      value: `${fmt(cop.value)} ${cop.value < cop.prev ? "↓" : "↑"}`,
      state: cop.value < 0 ? "bear" : cop.value < cop.prev ? "warn" : "bull",
      note: cop.value < 0 ? "ต่ำกว่าศูนย์ = โมเมนตัมยาวเป็นขาลง" : cop.value < cop.prev ? "ยังบวกแต่โค้งลง (เตือน)" : "บวกและขึ้น",
    },
    {
      name: "ROC 3/6/12 เดือน",
      value: `${sign(r3)}% / ${sign(r6)}% / ${sign(r12)}%`,
      state: negCount >= 2 ? "bear" : negCount === 1 ? "warn" : "bull",
      note: r12 < 0 ? "ผลตอบแทน 12 เดือนติดลบ = TSMOM พลิกลง" : `ติดลบ ${negCount}/3 กรอบ`,
    },
    {
      name: "ADX / DMI (14)",
      value: `ADX ${fmt(dm.adx)} · ${dm.mdi > dm.pdi ? "−DI>+DI" : "+DI>−DI"}`,
      state: dm.mdi > dm.pdi && dm.adx > 25 ? "bear" : dm.adx < 20 ? "neutral" : dm.pdi > dm.mdi ? "bull" : "warn",
      note: dm.adx > 25 ? "เทรนด์ชัด" : "เทรนด์อ่อน/ออกข้าง",
    },
    {
      name: "RSI 14 (รายวัน)",
      value: fmt(rsi),
      state: rsi >= 70 ? "bear" : rsi <= 30 ? "bull" : "neutral",
      note: rsi >= 70 ? "ซื้อมากเกินไป" : rsi <= 30 ? "ขายมากเกินไป (เด้งได้)" : "กลาง ๆ",
    },
    {
      name: "Mayer Multiple",
      value: fmt(mayer, 2),
      state: mayer >= 1.2 ? "bear" : mayer < 1 ? "neutral" : "warn",
      note: mayer >= 1.2 ? "ยืดเหนือเส้น 200 วันมาก" : mayer < 1 ? "อยู่ใต้เส้น 200 วัน" : "เหนือเส้นเล็กน้อย",
    },
    {
      name: "Drawdown จากจุดสูงสุด",
      value: `${sign(dd, 1)}% · ${c.length - 1 - peakIdx} วัน`,
      state: dd <= -20 ? "bear" : dd <= -10 ? "warn" : "neutral",
      note: dd <= -20 ? "เข้าเขตตลาดหมี" : dd <= -10 ? "ปรับฐาน" : "ใกล้จุดสูงสุด",
    },
  ];

  // key levels (nearest trigger first)
  const donchian20w = Math.min(...l.slice(-100));
  const high52w = Math.max(...h.slice(-252));
  const levels: KeyLevel[] = [
    { name: "จุดต่ำสุด 20 สัปดาห์ (แนวหลุด)", level: donchian20w, distPct: (close / donchian20w - 1) * 100 },
    { name: "เส้นค่าเฉลี่ย 200 วัน", level: ma200, distPct: (close / ma200 - 1) * 100 },
    { name: "จุดสูงสุด 52 สัปดาห์", level: high52w, distPct: (close / high52w - 1) * 100 },
  ].sort((a, b) => Math.abs(a.distPct) - Math.abs(b.distPct));

  return { indicators, levels };
}
