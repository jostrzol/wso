from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    manager_address: str
    token: str

    model_config = SettingsConfigDict(env_prefix="WSOTIMESRV_")


settings = Settings(**{})
