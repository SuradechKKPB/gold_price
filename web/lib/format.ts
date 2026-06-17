export const GRAMS_PER_BAHT_BAR = 15.244;

export function bahtWeight(grams: number): number {
  return grams / GRAMS_PER_BAHT_BAR;
}

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export const thb = (n: number) => nf0.format(n);
export const num = (n: number) => nf2.format(n);
export const pct = (n: number, digits = 0) => `${(n * 100).toFixed(digits)}%`;

export function bangkokDate(iso: string): string {
  return new Date(iso).toLocaleString("th-TH", {
    timeZone: "Asia/Bangkok",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function calDate(iso: string): string {
  return new Date(iso).toLocaleString("th-TH", {
    timeZone: "Asia/Bangkok",
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function newsDate(s: string): string {
  try {
    return new Date(s).toLocaleDateString("th-TH", { timeZone: "Asia/Bangkok", day: "numeric", month: "short" });
  } catch {
    return "";
  }
}
