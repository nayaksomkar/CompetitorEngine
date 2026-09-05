import json
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Config file path
    config_path: str = str(Path(__file__).parent.parent / "config.json")

    # LLM Brain Service (legacy, overridden by config.json)
    llm_service_url: str = "http://localhost:8000/chat"
    llm_timeout: int = 60
    llm_max_retries: int = 3

    # Scraper Service (Microservice 3)
    scraper_service_url: str = "http://localhost:8001"
    use_mock_scraper: bool = True

    # Service
    service_host: str = "0.0.0.0"
    service_port: int = 8001
    log_level: str = "INFO"

    # CORS
    allowed_origins: str = "https://nayaksomkar.github.io,http://localhost:3000,http://localhost:5173"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load configuration from config.json file."""
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @property
    def app_config(self) -> dict:
        """Get the full application config from config.json."""
        return self._config

    @property
    def llm_config(self) -> dict:
        """Get LLM configuration."""
        return self._config.get("llm", {})

    @property
    def providers(self) -> list[dict]:
        """Get list of LLM providers."""
        return self.llm_config.get("providers", [])

    @property
    def fallback_chain(self) -> list[str]:
        """Get provider fallback chain."""
        return self.llm_config.get("fallback_chain", [])

    @property
    def default_provider(self) -> str:
        """Get default provider name."""
        return self.llm_config.get("default_provider", "")

    def get_provider(self, name: str) -> dict | None:
        """Get provider config by name."""
        for provider in self.providers:
            if provider.get("name") == name:
                return provider
        return None

    def get_enabled_providers(self) -> list[dict]:
        """Get list of enabled providers."""
        return [p for p in self.providers if p.get("enabled", True)]

    @property
    def agents_config(self) -> dict:
        """Get agents configuration."""
        return self._config.get("agents", {})

    def get_agent_config(self, agent_name: str) -> dict:
        """Get config for a specific agent."""
        return self.agents_config.get(agent_name, {})

    @property
    def charts_config(self) -> dict:
        """Get charts/visualization configuration."""
        return self._config.get("charts", {})

    @property
    def scraper_config(self) -> dict:
        """Get scraper configuration."""
        return self._config.get("scraper", {})

    @property
    def cors_origins(self) -> list[str]:
        """Get CORS origins from config or env."""
        cors_config = self._config.get("cors", {})
        origins = cors_config.get("allowed_origins", [])
        if origins:
            return origins
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def session_config(self) -> dict:
        """Get session configuration."""
        return self._config.get("session", {})

    def get_service_config(self) -> dict:
        """Get service configuration."""
        return self._config.get("service", {})


settings = Settings()
