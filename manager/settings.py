from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    manager_name: str

    model_config = SettingsConfigDict(env_prefix="WSO_")


settings = Settings(**{})
