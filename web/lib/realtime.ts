import "server-only";

export interface Realtime {
  xauUsd: number; // international spot, USD/oz (real-time)
  usdThb: number;
  thbBar: number; // synthetic THB per baht-weight of 96.5% bar
  asOf: string; // ISO timestamp from the gold feed
}

// THB per 1 baht-weight of 96.5% bar = XAU(USD/oz) x USDTHB x (15.244/31.1035) x 0.965
const CONV = (15.244 / 31.1034768) * 0.965; // ≈ 0.47295

/** Real-time international gold (gold-api.com) + USD/THB (frankfurter.dev). Keyless. */
export async function fetchRealtimeGold(): Promise<Realtime | null> {
  try {
    const [g, f] = await Promise.all([
      fetch("https://api.gold-api.com/price/XAU", { next: { revalidate: 60 } }),
      fetch("https://api.frankfurter.dev/v1/latest?base=USD&symbols=THB", { next: { revalidate: 600 } }),
    ]);
    const gj = (await g.json()) as { price: number; updatedAt?: string };
    const fj = (await f.json()) as { rates: { THB: number } };
    const xau = Number(gj.price);
    const thb = Number(fj.rates?.THB);
    if (!xau || !thb) return null;
    return { xauUsd: xau, usdThb: thb, thbBar: xau * thb * CONV, asOf: gj.updatedAt ?? "" };
  } catch {
    return null;
  }
}
