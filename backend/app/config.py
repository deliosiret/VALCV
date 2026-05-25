from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://valcv:valcv_dev_password@localhost:15439/valcv"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"
    upload_dir: str = "./uploads"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
