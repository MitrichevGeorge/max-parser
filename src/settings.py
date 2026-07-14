from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ONEME_DEVICE_ID: str = Field(default="q")
    ONEME_AUTH: dict = Field(default_factory=dict)
    LOGS_PORT: int = Field(default=8080)

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8"
    )

    @model_validator(mode="after")
    def check_required_fields(self):
        if self.ONEME_DEVICE_ID == "q" or not self.ONEME_AUTH.get("token"):
            raise ValueError("Pls check .env")
        return self

stg = Settings()
