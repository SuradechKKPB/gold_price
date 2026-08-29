// US Dollar Index (DXY) — conditional next-12-month THB-gold stats by DXY band.
// Stats are from the PRE-2020 sample (etl/dxy.py, intl basis) to avoid look-ahead: a
// strong dollar preceded the weakest forward gold returns, so sell-pressure is MONOTONE
// INCREASING in the dollar. (The earlier full-sample table had it backwards — it learned
// from 2020–26, where a high DXY coincided with a THB-gold melt-up, and pinned the
// component low.) Tails (<80, >110) are thin — interpret with care.
//
// HAND-COPIED SNAPSHOT: these counts and averages are the printed output of
// `uv run python -m etl.dxy`, transcribed. Nothing regenerates them, so if that study is
// ever re-run this table must be updated by hand or the page will quietly disagree with
// the score. `sell` must stay identical to DOLLAR_SELL in etl/dxy.py.

export interface DxyBand {
  band: string;
  n: number;
  avgRet: number; // avg next-12m THB-gold return %
  avgLoss: number; // avg of negative 12m returns %
  posPct: number; // % of windows positive
  retDD: number | null; // avg return ÷ avg max-drawdown
  sell: number; // dollar-regime sell-pressure folded into the score
}

export const DXY_TABLE: DxyBand[] = [
  { band: "<80", n: 47, avgRet: 7.5, avgLoss: -11.4, posPct: 70, retDD: 0.58, sell: 30 },
  { band: "80–90", n: 61, avgRet: 7.5, avgLoss: -8.9, posPct: 57, retDD: 0.62, sell: 40 },
  { band: "90–100", n: 42, avgRet: 3.0, avgLoss: -4.3, posPct: 48, retDD: 0.39, sell: 55 },
  { band: "100–110", n: 6, avgRet: 0.3, avgLoss: -2.6, posPct: 33, retDD: 0.05, sell: 68 },
  { band: ">110", n: 0, avgRet: 0, avgLoss: 0, posPct: 0, retDD: null, sell: 75 },
];

export function bandOf(dxy: number): string {
  if (dxy < 80) return "<80";
  if (dxy < 90) return "80–90";
  if (dxy < 100) return "90–100";
  if (dxy < 110) return "100–110";
  return ">110";
}

/** Live reconstructed DXY from ECB FX (frankfurter), keyless. */
export async function fetchCurrentDxy(): Promise<number | null> {
  try {
    const res = await fetch("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR,JPY,GBP,CAD,SEK,CHF", {
      next: { revalidate: 1800 },
    });
    const r = (await res.json()).rates as Record<string, number>;
    const eurusd = 1 / r.EUR, gbpusd = 1 / r.GBP;
    const dxy =
      50.14348112 * eurusd ** -0.576 * r.JPY ** 0.136 * gbpusd ** -0.119 * r.CAD ** 0.091 * r.SEK ** 0.042 * r.CHF ** 0.036;
    return Number.isFinite(dxy) ? dxy : null;
  } catch {
    return null;
  }
}
