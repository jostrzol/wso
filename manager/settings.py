from pydantic import MongoDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    manager_name: str
    connection_string: MongoDsn

    model_config = SettingsConfigDict(env_prefix="WSOMGR_")


settings = Settings(**{})
