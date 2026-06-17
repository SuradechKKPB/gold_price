import PriceChart from "@/components/PriceChart";
import { BacktestTable, BucketBars, ScoreGauge, Stat, VerdictChip } from "@/components/ui";
import { drawdown, sma } from "@/lib/indicators";
import { bahtWeight, bangkokDate, num, pct, thb } from "@/lib/format";
import { getBacktest, getLatestSignal, getLatestTick, getPriceHistory } from "@/lib/queries";

export const revalidate = 1800;

const SIGNAL_LABELS: Record<string, string> = {
  below_chandelier: "below weekly Chandelier stop",
  below_donchian_10w: "below 10-week low",
  below_donchian_20w: "below 20-week low",
  death_cross: "50/200 death cross",
  below_200dma: "below 200-day MA",
  rsi_weekly_gt70: "weekly RSI > 70",
  stretch_gt18pct: "> 18% above 200-DMA",
  pctb_gt1: "above upper Bollinger band",
  macd_bearish: "weekly MACD bearish",
};

export default async function Page() {
  const grams = Number(process.env.GOLD_GRAMS ?? 700);
  const bw = bahtWeight(grams);

  const [signal, tick, prices, runs] = await Promise.all([
    getLatestSignal(),
    getLatestTick(),
    getPriceHistory(),
    getBacktest(252),
  ]);

  const buyIn = tick?.bar_buy ?? prices.at(-1)?.bar_buy_close ?? 0;
  const holdingValue = bw * buyIn;

  const priceSeries = prices.map((r) => ({ time: r.trade_date, value: r.bar_buy_close }));
  const ma200 = sma(prices, 200);
  const dd = drawdown(prices, "2011-01-01", "2014-12-31");

  const buckets = signal
    ? [
        { label: "trend break", value: signal.trend_break },
        { label: "overbought", value: signal.overbought },
        { label: "momentum", value: signal.momentum },
        { label: "seasonality", value: signal.seasonality },
      ]
    : [];

  return (
    <main style={{ maxWidth: 980, margin: "0 auto", padding: "48px 24px 80px" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="serif" style={{ fontSize: 34, fontWeight: 500, letterSpacing: -0.5 }}>
            Gold — when to sell
          </h1>
          <p className="muted" style={{ marginTop: 4, fontSize: 14 }}>
            96.5% bar · THB buy-in · technical + fundamental sell-timing, backtested on 20 years
          </p>
        </div>
        {tick && (
          <div className="muted mono" style={{ fontSize: 12, textAlign: "right" }}>
            GTA round {tick.seq}
            <br />
            {bangkokDate(tick.as_time)}
          </div>
        )}
      </header>

      {/* Hero */}
      <section className="panel" style={{ padding: 28, marginTop: 28, display: "grid", gap: 28, gridTemplateColumns: "1.1fr 1fr" }}>
        <div>
          <div className="muted" style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 0.6 }}>
            {grams} g ({num(bw)} baht-weight) at today&apos;s buy-in
          </div>
          <div className="mono serif" style={{ fontSize: 44, marginTop: 8, color: "var(--gold)" }}>
            ฿{thb(holdingValue)}
          </div>
          <div className="muted mono" style={{ fontSize: 13, marginTop: 6 }}>
            buy-in {thb(buyIn)} /baht-weight{tick ? ` · spot $${num(tick.gold_spot_usd)} · USDTHB ${num(tick.baht_per_usd)}` : ""}
          </div>
          {signal && (
            <div style={{ marginTop: 20, display: "flex", alignItems: "center", gap: 12 }}>
              <VerdictChip verdict={signal.verdict} />
              {signal.active_signals.length > 0 && (
                <span className="muted" style={{ fontSize: 13 }}>
                  {signal.active_signals.map((s) => SIGNAL_LABELS[s] ?? s).join(" · ")}
                </span>
              )}
            </div>
          )}
        </div>
        <div>
          {signal && <ScoreGauge score={signal.sell_pressure} />}
          <div style={{ marginTop: 20 }}>
            <BucketBars buckets={buckets} />
          </div>
        </div>
      </section>

      {/* Price chart */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
            Bar buy-in, 2006–today
          </h2>
          <span className="muted mono" style={{ fontSize: 12 }}>
            gold line = 200-day MA
          </span>
        </div>
        <div style={{ marginTop: 12 }}>
          <PriceChart
            price={priceSeries}
            ma200={ma200}
            marker={dd.dropPct < 0 ? { time: dd.troughDate, text: `2013 ${pct(dd.dropPct)}` } : undefined}
          />
        </div>
      </section>

      {/* Backtest */}
      <section className="panel" style={{ padding: 24, marginTop: 20 }}>
        <h2 className="serif" style={{ fontSize: 20, fontWeight: 500 }}>
          What worked, 12-month windows (2010–2026)
        </h2>
        <p className="muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
          Capture = share of each window&apos;s price range realized. Over a 16-year bull market, simply{" "}
          <b style={{ color: "var(--text)" }}>holding</b> won — and our composite score was the best <i>active</i> rule,
          beating mechanical DCA-out. The averages flatter patience because the trend rarely broke; the score earns its
          keep on the exception.
        </p>
        <div style={{ marginTop: 16 }}>
          <BacktestTable runs={runs} />
        </div>
        {dd.dropPct < 0 && (
          <div className="panel" style={{ background: "var(--panel2)", padding: 16, marginTop: 16 }}>
            <div className="muted" style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: 0.6 }}>
              The exception — 2013
            </div>
            <p style={{ fontSize: 14, marginTop: 6, lineHeight: 1.5 }}>
              From {bangkokDate(dd.peakDate).split(",")[0]} the bar price fell{" "}
              <b className="mono" style={{ color: "var(--red)" }}>{pct(dd.dropPct)}</b> to{" "}
              {bangkokDate(dd.troughDate).split(",")[0]} ({thb(dd.peak)} → {thb(dd.trough)} /baht-weight). This is the
              regime break a sell-timing tool exists to catch — invisible in the bull-dominated medians above.
            </p>
          </div>
        )}
      </section>

      <footer className="muted" style={{ fontSize: 12, marginTop: 28, lineHeight: 1.5 }}>
        Decision support, not investment advice. Signals are calibrated in-sample on overlapping windows (wide confidence
        intervals); past behavior does not guarantee future results. Source: Gold Traders Association of Thailand.
      </footer>
    </main>
  );
}
