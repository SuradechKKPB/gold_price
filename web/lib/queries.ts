import "server-only";
import { supabase } from "./supabase";
import type { BacktestRun, PriceRow, SignalRow, TickRow } from "./types";

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
  fetchAll<PriceRow>("gold_price_daily", "trade_date,bar_buy_close", "trade_date");

export const getScoreHistory = () =>
  fetchAll<{ trade_date: string; sell_pressure: number }>("signals_daily", "trade_date,sell_pressure", "trade_date");

export async function getBacktest(horizonDays: number): Promise<BacktestRun[]> {
  const { data } = await supabase
    .from("backtest_runs")
    .select("strategy,horizon_days,median_capture_pct,median_regret_thb,win_rate_vs_dca")
    .eq("horizon_days", horizonDays)
    .order("median_capture_pct", { ascending: false });
  return (data as BacktestRun[]) ?? [];
}
