import type { PriceRow } from "./types";

export interface Point {
  time: string;
  value: number;
}

/** Simple moving average aligned to the input dates (null until enough history). */
export function sma(rows: PriceRow[], window: number): Point[] {
  const out: Point[] = [];
  let sum = 0;
  for (let i = 0; i < rows.length; i++) {
    sum += rows[i].bar_buy_close;
    if (i >= window) sum -= rows[i - window].bar_buy_close;
    if (i >= window - 1) out.push({ time: rows[i].trade_date, value: +(sum / window).toFixed(2) });
  }
  return out;
}

/** Largest peak-to-trough drop within a date range — used to surface the 2013-style tail. */
export function drawdown(rows: PriceRow[], from: string, to: string) {
  const seg = rows.filter((r) => r.trade_date >= from && r.trade_date <= to);
  let peak = -Infinity;
  let peakDate = "";
  let worst = 0;
  let result = { dropPct: 0, peak: 0, trough: 0, peakDate: "", troughDate: "" };
  for (const r of seg) {
    if (r.bar_buy_close > peak) {
      peak = r.bar_buy_close;
      peakDate = r.trade_date;
    }
    const drop = (r.bar_buy_close - peak) / peak;
    if (drop < worst) {
      worst = drop;
      result = {
        dropPct: drop,
        peak,
        trough: r.bar_buy_close,
        peakDate,
        troughDate: r.trade_date,
      };
    }
  }
  return result;
}
