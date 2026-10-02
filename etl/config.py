"""Runtime settings, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Supabase — service-role key is server/ETL only and bypasses RLS.
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Alerts (event-driven on verdict transitions; see etl/alerts.py — no numeric threshold)
    line_channel_access_token: str = ""
    line_channel_access_token_2: str = ""  # fallback OA, used when the primary hits its monthly quota
    dashboard_url: str = "https://gold-price-gamma.vercel.app"

    # Holding config — drives the headline THB figure and the backtest unit.
    gold_grams: float = 1000.0
    gold_type: str = "bar"          # bar = 96.5% ทองคำแท่ง
    bar_spread_thb: float = 200.0   # modeled buy-in = sell - spread when live bid unknown

    # Sell plan (etl/plan.py): PACE is the seller's call, the score only picks the days.
    # Empty start/deadline or 0 grams switches the plan off.
    plan_start: str = "2026-10-02"
    plan_deadline: str = "2026-12-30"   # 31 Dec is a Thai holiday: the last session must be before it
    plan_sell_grams: float = 500.0  # at least half of the holding by the deadline
    plan_tranches: int = 5
    # The LINE OA broadcasts to every follower, so plan progress (personal) stays off it
    # unless switched on; the market zone is broadcast either way.
    plan_in_broadcast: bool = False

    # Personal overlay (etl/advice.py). All optional — 0 disables that line.
    target_thb: float = 0.0         # total proceeds goal for the whole holding (0 = unset)
    cost_basis_thb_per_baht: float = 0.0  # avg buy price per baht-weight (0 = unset), for P/L framing

    # Thai gold weight conventions (grams per 1 baht-weight).
    grams_per_baht_bar: float = 15.244
    grams_per_baht_ornament: float = 15.16

    @property
    def has_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def baht_weight(self) -> float:
        """The holding expressed in baht-weight units (what GTA quotes)."""
        gpb = self.grams_per_baht_bar if self.gold_type == "bar" else self.grams_per_baht_ornament
        return self.gold_grams / gpb


settings = Settings()
