/** v4 verdicts grade the DAY (etl/signals.py VERDICTS). */
export type Verdict = "weak" | "neutral" | "rich" | "very_rich";

/** signals_daily kept its v3 columns (no migration); in v4 they carry:
 *  sell_pressure = rich (0-100) · overbought = percentile of price vs SMA50 ·
 *  momentum = percentile of the rally from the 40-bar low · trend_break = brake depth. */
export interface SignalRow {
  trade_date: string;
  sell_pressure: number;
  trend_break: number;
  overbought: number;
  momentum: number;
  seasonality: number | null; // retired in v4
  fa_score: number | null; // retired in v4 (DXY band)
  verdict: Verdict;
  active_signals: string[];
}

export interface PriceRow {
  trade_date: string;
  bar_buy_close: number;
  bar_sell_high: number;
  bar_sell_low: number;
}

export interface TickRow {
  as_time: string;
  seq: number;
  bar_buy: number;
  gold_spot_usd: number;
  baht_per_usd: number;
}

/** One regime's numbers for a strategy (etl/backtest.py summarize). */
export interface RegimeStats {
  edge_pct: number | null; // avg sale price vs the plan at slot ends, %
  win_pct: number | null;
  avg_day: number | null; // average trading day of the sales: later = more trend exposure
  skill_pct: number | null; // fill vs the centred ±63-day average: drift-neutral day skill
  n: number;
}

export interface BacktestRun {
  strategy: string;
  horizon_days: number;
  median_capture_pct: number;
  median_regret_thb: number;
  win_rate_vs_dca: number | null;
  params?: {
    by_regime?: Record<string, RegimeStats>;
    vs_random_day?: Record<string, RegimeStats> | null;
    n_eff?: number;
    tranches?: number;
    score_version?: number;
  } | null;
}

/** The trailing-stop state the Python score reads (macro_daily, written by etl.compute).
 *  Published rather than recomputed here so the panel and the score cannot disagree. */
export interface TrailState {
  ddFromHigh: number; // fraction >= 0; 0 at a new high
  recentHigh: number; // THB level the drawdown is measured from (40-bar high)
  brakeDd: number | null; // drawdown at which a weak day engages the brake (null before v4)
}

/** The sell plan's decision for the next session (etl/plan.py evaluate, state plan_today). */
export interface PlanStatus {
  as_of: string;
  action: "sell" | "wait" | "hold" | "done" | "ended";
  reason: string;
  tranche?: number;
  n_tranches: number;
  tranches_sold: number;
  sold_grams: number;
  sell_grams: number;
  holding_grams: number;
  remaining_grams?: number;
  next_grams?: number;
  due_date?: string | null;
  wait_until?: string | null;
  count?: number; // tranches to sell this session; >1 only when behind at the deadline
  schedule?: (string | null)[];
  start: string;
  deadline: string;
  verdict: string;
  rich: number | null;
}
