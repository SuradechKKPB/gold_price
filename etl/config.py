"""Runtime settings, loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Supabase — service-role key is server/ETL only and bypasses RLS.
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # External data
    fred_api_key: str = ""

    # Alerts (event-driven on verdict transitions; see etl/alerts.py — no numeric threshold)
    line_channel_access_token: str = ""
    dashboard_url: str = "https://gold-price-gamma.vercel.app"

    # Holding config — drives the headline THB figure and the backtest unit.
    gold_grams: float = 700.0
    gold_type: str = "bar"          # bar = 96.5% ทองคำแท่ง
    bar_spread_thb: float = 200.0   # modeled buy-in = sell - spread when live bid unknown

    # Sell-campaign config (etl/advice.py). All optional — empty/0 disables that overlay.
    sell_window_start: str = ""     # ISO date the 3-12m exit window opened; enables deadline decay
    sell_window_months: int = 12    # window length; the sell bar decays to ~trim by expiry
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
