from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(validation_alias="BOT_TOKEN")
    admin_ids_raw: str = Field(default="", validation_alias="ADMIN_IDS")
    sam_api_base: str = Field(default="", validation_alias="SAM_API_BASE")
    sam_api_key: str = Field(default="", validation_alias="SAM_API_KEY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("admin_ids_raw", mode="before")
    @classmethod
    def normalize_admin_ids(cls, value: str | None) -> str:
        return "" if value is None else str(value)

    @property
    def admin_ids(self) -> set[int]:
        if not self.admin_ids_raw.strip():
            return set()
        return {int(item.strip()) for item in self.admin_ids_raw.split(",") if item.strip()}


def get_settings() -> Settings:
    return Settings()
