export type Verdict = "hold" | "trim" | "sell_tranche" | "sell";

export interface SignalRow {
  trade_date: string;
  sell_pressure: number;
  trend_break: number;
  overbought: number;
  momentum: number;
  seasonality: number;
  verdict: Verdict;
  active_signals: string[];
}

export interface PriceRow {
  trade_date: string;
  bar_buy_close: number;
}

export interface TickRow {
  as_time: string;
  seq: number;
  bar_buy: number;
  gold_spot_usd: number;
  baht_per_usd: number;
}

export interface BacktestRun {
  strategy: string;
  horizon_days: number;
  median_capture_pct: number;
  median_regret_thb: number;
  win_rate_vs_dca: number | null;
}
