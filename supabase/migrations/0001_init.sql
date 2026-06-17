-- gold-thb sell-timing dashboard — core schema
-- convention: timestamps stored utc; trade_date is the asia/bangkok calendar date.
-- rls is enabled deny-by-default; the etl uses the service-role key (bypasses rls).
-- family read policies are added in the auth phase.

create extension if not exists "uuid-ossp" with schema extensions;

-- raw live snapshots from gta /Latest.
-- builds a real buy-in (bid) series + spot + fx over time; bar_buy is the seller-relevant price.
create table if not exists gold_price_ticks (
    id             bigint generated always as identity primary key,
    as_time        timestamptz not null,             -- gta asTime (bangkok wall-clock, tz-aware)
    seq            int not null,                      -- round number (ครั้งที่) within the day
    bar_buy        numeric(12, 2),                    -- bL_BuyPrice  (รับซื้อ — what you SELL into)
    bar_sell       numeric(12, 2),                    -- bL_SellPrice (ขายออก)
    ornament_buy   numeric(12, 2),                    -- oM965_BuyPrice
    gold9999_buy   numeric(12, 2),                    -- oM9999_BuyPrice
    gold_spot_usd  numeric(12, 2),                    -- goldSpot (xau/oz)
    baht_per_usd   numeric(10, 4),                    -- bahtPerUSD
    chg_prev_row   numeric(12, 2),
    chg_prev_day   numeric(12, 2),
    gold_price_id  bigint,                            -- gta goldPriceID
    fetched_at     timestamptz not null default now(),
    unique (as_time, seq)
);

-- canonical daily series used for indicators + backtest.
create table if not exists gold_price_daily (
    trade_date      date primary key,                 -- asia/bangkok calendar date
    bar_sell_open   numeric(12, 2),                   -- from /ohlc (sell-out series)
    bar_sell_high   numeric(12, 2),
    bar_sell_low    numeric(12, 2),
    bar_sell_close  numeric(12, 2),
    bar_buy_close   numeric(12, 2),                   -- live bid when known, else sell_close - spread
    gold_spot_usd   numeric(12, 2),
    baht_per_usd    numeric(10, 4),
    source          text not null default 'gta_ohlc',
    ingested_at     timestamptz not null default now()
);

-- macro / fundamental daily drivers (long format).
create table if not exists macro_daily (
    trade_date   date not null,
    series       text not null,                       -- dfii10 | dtwexbgs | cpi_yoy | dexthus | gld_tonnes | cot_mm_net
    value        numeric,
    source       text,
    ingested_at  timestamptz not null default now(),
    primary key (trade_date, series)
);

-- computed technical indicators (long format).
create table if not exists indicators_daily (
    trade_date   date not null,
    timeframe    text not null default 'weekly',      -- weekly | daily
    indicator    text not null,                       -- rsi14 | ma200 | chandelier_22_3 | donchian_low_10w | ...
    value        numeric,
    params       jsonb not null default '{}',
    computed_at  timestamptz not null default now(),
    primary key (trade_date, timeframe, indicator, params)
);

-- daily 0-100 sell-pressure score + verdict.
create table if not exists signals_daily (
    trade_date      date primary key,
    sell_pressure   numeric(5, 2),                    -- composite 0-100
    trend_break     numeric(5, 2),
    overbought      numeric(5, 2),
    momentum        numeric(5, 2),
    seasonality     numeric(5, 2),
    fa_score        numeric(5, 2),                    -- fundamental composite
    verdict         text,                             -- hold | trim | sell_tranche | sell
    active_signals  jsonb not null default '[]',
    computed_at     timestamptz not null default now()
);

-- backtest runs (precomputed offline) + per-window results.
create table if not exists backtest_runs (
    id                  uuid primary key default extensions.uuid_generate_v4(),
    strategy            text not null,                -- trailing_stop_peak | dca_out | random_day | window_end
    params              jsonb not null default '{}',
    horizon_days        int not null,
    start_date          date,
    end_date            date,
    median_thb          numeric,
    median_capture_pct  numeric,
    median_regret_thb   numeric,
    p90_regret_thb      numeric,
    win_rate_vs_dca     numeric,
    run_at              timestamptz not null default now()
);

create table if not exists backtest_windows (
    run_id        uuid references backtest_runs (id) on delete cascade,
    window_start  date not null,
    window_end    date not null,
    sell_date     date,
    sell_price    numeric,
    window_min    numeric,
    window_max    numeric,
    capture_pct   numeric,
    regret_thb    numeric,
    primary key (run_id, window_start)
);

create index if not exists idx_macro_series on macro_daily (series, trade_date);
create index if not exists idx_ticks_as_time on gold_price_ticks (as_time desc);
create index if not exists idx_bt_windows_run on backtest_windows (run_id);

alter table gold_price_ticks enable row level security;
alter table gold_price_daily enable row level security;
alter table macro_daily enable row level security;
alter table indicators_daily enable row level security;
alter table signals_daily enable row level security;
alter table backtest_runs enable row level security;
alter table backtest_windows enable row level security;
