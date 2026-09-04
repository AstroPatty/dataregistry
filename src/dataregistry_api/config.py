from pydantic import Field, NonNegativeInt
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# `psycopg2` is the driver the core `dataregistry` package already depends on,
# so the API reuses it rather than pulling in a second libpq binding.
DRIVER_NAME = "postgresql+psycopg2"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="dataregistry_api_")
    database_host: str
    database_name: str = Field(default="dataregistry")
    database_port: NonNegativeInt = Field(default=5432)
    database_username: str | None = Field(default=None)
    database_password: str | None = Field(default=None)


class DatabasePoolSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="dataregistry_api_")
    pool_size: NonNegativeInt = Field(default=5)
    max_overflow: NonNegativeInt = Field(default=10)
    pool_timeout: NonNegativeInt = Field(default=5)
    pool_recycle: NonNegativeInt = Field(default=1800)


def get_database_connection_url(settings: DatabaseSettings | None = None) -> URL:
    """Build the SQLAlchemy URL for the registry database.

    Parameters
    ----------
    settings : DatabaseSettings, optional
        Pre-built settings. When omitted they are read from the environment
        (``DATAREGISTRY_API_*``).

    Returns
    -------
    sqlalchemy.engine.URL
    """
    if settings is None:
        settings = DatabaseSettings()

    if settings.database_password is not None and settings.database_username is None:
        raise ValueError("database_password set without database_username")

    return URL.create(
        drivername=DRIVER_NAME,
        username=settings.database_username,
        password=settings.database_password,
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
    )
