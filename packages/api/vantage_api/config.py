"""Application settings, loaded from the environment with sane local defaults."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Vantage API.

    Every field can be overridden by an environment variable of the same name
    (case-insensitive) or by an entry in a local `.env` file.
    """

    database_url: str = "postgresql+asyncpg://vantage:vantage@localhost:5432/vantage"
    api_key: str = "dev-key-change-me"
    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Module-level singleton: import this rather than constructing Settings() again,
# so the .env file is read exactly once per process.
settings = Settings()
