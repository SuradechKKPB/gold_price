import "server-only";
import { supabase } from "./supabase";
import type { BacktestRun, PriceRow, SignalRow, TickRow, TrailState } from "./types";

async function fetchAll<T>(table: string, cols: string, order: string): Promise<T[]> {
  const out: T[] = [];
  const size = 1000;
  for (let from = 0; ; from += size) {
    const { data, error } = await supabase.from(table).select(cols).order(order).range(from, from + size - 1);
    if (error) throw error;
    out.push(...((data ?? []) as T[]));
    if (!data || data.length < size) break;
  }
  return out;
}

export async function getLatestSignal(): Promise<SignalRow | null> {
  const { data } = await supabase
    .from("signals_daily")
    .select("*")
    .order("trade_date", { ascending: false })
    .limit(1)
    .maybeSingle();
  return (data as SignalRow) ?? null;
}

export async function getLatestTick(): Promise<TickRow | null> {
  const { data } = await supabase
    .from("gold_price_ticks")
    .select("as_time,seq,bar_buy,gold_spot_usd,baht_per_usd")
    .order("as_time", { ascending: false })
    .limit(1)
    .maybeSingle();
  return (data as TickRow) ?? null;
}

export const getPriceHistory = () =>
  fetchAll<PriceRow>("gold_price_daily", "trade_date,bar_buy_close,bar_sell_high,bar_sell_low", "trade_date");

/** International (world) gold in THB — the score's price basis (macro_daily series).
 *  A single daily value, mapped to the PriceRow shape (high=low=close) for the TA helpers. */
export async function getIntlHistory(): Promise<PriceRow[]> {
  const out: PriceRow[] = [];
  const size = 1000;
  for (let from = 0; ; from += size) {
    const { data, error } = await supabase
      .from("macro_daily")
      .select("trade_date,value")
      .eq("series", "gold_intl_thb")
      .order("trade_date")
      .range(from, from + size - 1);
    if (error) throw error;
    const rows = (data ?? []) as { trade_date: string; value: number }[];
    for (const r of rows) out.push({ trade_date: r.trade_date, bar_buy_close: r.value, bar_sell_high: r.value, bar_sell_low: r.value });
    if (rows.length < size) break;
  }
  return out;
}

export const getScoreHistory = () =>
  fetchAll<{ trade_date: string; sell_pressure: number }>("signals_daily", "trade_date,sell_pressure", "trade_date");

export async function getBacktest(horizonDays: number): Promise<BacktestRun[]> {
  const { data } = await supabase
    .from("backtest_runs")
    .select("strategy,horizon_days,median_capture_pct,median_regret_thb,win_rate_vs_dca,params")
    .eq("horizon_days", horizonDays)
    .order("median_capture_pct", { ascending: false });
  return (data as BacktestRun[]) ?? [];
}

/** Distance to the recent high that the score's trailing break is measured against.
 *
 *  Read, never recomputed: the lookback (40 bars) and the 3%/8% break band live in
 *  etl/indicators.py and etl/signals.py, and a second implementation here would drift
 *  from the score the moment either constant moved. Returns null before etl.compute has
 *  published the series (or if only one of the two rows exists) so the caller can hide
 *  the panel rather than render half of it. */
export async function getTrailState(): Promise<TrailState | null> {
  const latest = async (series: string): Promise<number | null> => {
    const { data } = await supabase
      .from("macro_daily")
      .select("value")
      .eq("series", series)
      .order("trade_date", { ascending: false })
      .limit(1)
      .maybeSingle();
    const v = (data as { value: number } | null)?.value;
    return typeof v === "number" ? v : null;
  };
  const [ddFromHigh, recentHigh] = await Promise.all([latest("dd_from_high"), latest("recent_high_40")]);
  if (ddFromHigh === null || recentHigh === null) return null;
  return { ddFromHigh, recentHigh };
}
