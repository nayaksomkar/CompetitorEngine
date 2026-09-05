from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM Brain Service
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
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
