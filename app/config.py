import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """CompetitorEngine runtime settings.

    Service URLs come from environment variables (LLMPING_URL,
    WEBHUNTER_URL). When unset, the orchestrator auto-discovers
    sibling containers at runtime (see app/services/discovery.py).
    Set REQUIRE_SERVICE_URLS=true to restore the old fail-fast
    behavior for production deployments where a missing upstream is
    a misconfiguration rather than an expected cold start.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Config file path (only used for service{} and cors{} blocks)
    config_path: str = str(Path(__file__).parent.parent / "config.json")

    # Service URLs — optional when REQUIRE_SERVICE_URLS is false.
    llmping_url: str = ""
    llmping_timeout: int = 60
    llmping_api_key: str | None = None

    webhunter_url: str = ""
    webhunter_timeout: int = 30

    # Discovery controls
    require_service_urls: bool = False
    discovery_timeout_seconds: float = 2.0
    discovery_candidates_llmping: str = ""
    discovery_candidates_webhunter: str = ""

    # Service (host/port/log_level)
    service_host: str = "0.0.0.0"
    service_port: int = 8001
    log_level: str = "INFO"

    # CORS env override
    allowed_origins: str = (
        "https://nayaksomkar.github.io,http://localhost:3000,http://localhost:5173"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Optional fail-fast: opt-in via REQUIRE_SERVICE_URLS=true.
        # By default the orchestrator boots without URLs and resolves
        # them on demand via service discovery.
        if self.require_service_urls:
            if not self.llmping_url:
                raise RuntimeError(
                    "LLMPING_URL is not set. REQUIRE_SERVICE_URLS is "
                    "enabled; refusing to start without an LLMPing "
                    "endpoint."
                )
            if not self.webhunter_url:
                raise RuntimeError(
                    "WEBHUNTER_URL is not set. REQUIRE_SERVICE_URLS is "
                    "enabled; refusing to start without a WebHunter "
                    "endpoint."
                )

    def _load_config(self) -> dict:
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @property
    def app_config(self) -> dict:
        return self._load_config()

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def get_service_config(self) -> dict:
        return self.app_config.get("service", {})


settings = Settings()
