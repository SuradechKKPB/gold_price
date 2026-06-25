// US Dollar Index (DXY) — conditional next-12-month THB-gold stats by DXY band.
// Computed offline from reconstructed DXY (ECB FX) + GTA THB gold 2006–2026
// (etl/dxy.py). The current DXY level is fetched live and folded into the score
// (dollar-regime component). Tails (<80, >110) are thin — interpret with care.

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
  { band: "<80", n: 47, avgRet: 6.3, avgLoss: -10.3, posPct: 68, retDD: 0.53, sell: 70 },
  { band: "80–90", n: 60, avgRet: 8.2, avgLoss: -7.4, posPct: 57, retDD: 0.84, sell: 58 },
  { band: "90–100", n: 81, avgRet: 8.9, avgLoss: -3.9, posPct: 68, retDD: 1.16, sell: 45 },
  { band: "100–110", n: 40, avgRet: 23.6, avgLoss: -2.3, posPct: 90, retDD: 7.09, sell: 18 },
  { band: ">110", n: 2, avgRet: 11.5, avgLoss: 0.0, posPct: 100, retDD: 8.21, sell: 15 },
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
