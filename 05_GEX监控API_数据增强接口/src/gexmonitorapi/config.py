from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_token: str = Field(
        default="dev-token-change-me",
        validation_alias=AliasChoices("API_TOKEN", "GEXMONITOR_API_TOKEN"),
    )
    asset: str = "BTC"
    base_url: str = "https://gexmonitor.com"
    refresh_interval_seconds: int = Field(
        # 30 min by default: fewer headless-browser memory spikes on small hosts.
        default=1800,
        validation_alias=AliasChoices("REFRESH_INTERVAL_SECONDS", "GEXMONITOR_REFRESH_INTERVAL_SECONDS"),
    )
    request_timeout_seconds: int = Field(
        default=45,
        validation_alias=AliasChoices("REQUEST_TIMEOUT_SECONDS", "GEXMONITOR_REQUEST_TIMEOUT_SECONDS"),
    )
    cache_file: Path = Field(
        default=Path(".cache/gexmonitorapi/cache.json"),
        validation_alias=AliasChoices("CACHE_FILE", "GEXMONITOR_CACHE_FILE"),
    )
    user_agent: str = Field(
        default="gexmonitorapi/0.1 (+https://gexmonitor.com public page metric monitor)",
        validation_alias=AliasChoices("USER_AGENT", "GEXMONITOR_USER_AGENT"),
    )
    enable_background_refresh: bool = Field(
        default=True,
        validation_alias=AliasChoices("ENABLE_BACKGROUND_REFRESH", "GEXMONITOR_ENABLE_BACKGROUND_REFRESH"),
    )
    refresh_on_startup: bool = Field(
        default=False,
        validation_alias=AliasChoices("REFRESH_ON_STARTUP", "GEXMONITOR_REFRESH_ON_STARTUP"),
    )
    browser_no_sandbox: bool = Field(
        default=False,
        validation_alias=AliasChoices("BROWSER_NO_SANDBOX", "GEXMONITOR_BROWSER_NO_SANDBOX"),
    )
    # Blocks font/image/media/stylesheet AND websocket requests. Saves memory but
    # can starve websocket-fed data, so default off; enable only if metrics still load.
    browser_disable_resources: bool = Field(
        default=False,
        validation_alias=AliasChoices("BROWSER_DISABLE_RESOURCES", "GEXMONITOR_BROWSER_DISABLE_RESOURCES"),
    )
    # The analytics panels are websocket-fed and render a few seconds after the DOM
    # loads, so we wait a fixed settle time rather than for network idle (which never
    # arrives while the websocket streams). ~12s reliably renders the metrics.
    browser_wait_ms: int = Field(
        default=12000,
        validation_alias=AliasChoices("BROWSER_WAIT_MS", "GEXMONITOR_BROWSER_WAIT_MS"),
    )
    browser_network_idle: bool = Field(
        default=False,
        validation_alias=AliasChoices("BROWSER_NETWORK_IDLE", "GEXMONITOR_BROWSER_NETWORK_IDLE"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,
    )

