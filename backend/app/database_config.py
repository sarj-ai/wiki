from typing import Annotated, Self

from pydantic import PostgresDsn, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
InstanceConnectionName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*:[a-z0-9-]+:[a-z][a-z0-9-]*$")
]


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: PostgresDsn | None = None
    DB_INSTANCE_CONNECTION_NAME: InstanceConnectionName | None = None
    DB_IAM_USER: NonEmptyStr | None = None
    DB_NAME: NonEmptyStr = "sarj_wiki"

    @model_validator(mode="after")
    def exactly_one_database_path(self) -> Self:
        dsn = self.DATABASE_URL is not None
        connector = self.DB_INSTANCE_CONNECTION_NAME is not None or self.DB_IAM_USER is not None
        if dsn == connector:
            msg = (
                "Configure exactly one database path: DATABASE_URL,"
                " or DB_INSTANCE_CONNECTION_NAME + DB_IAM_USER"
            )
            raise ValueError(msg)
        if connector and (self.DB_INSTANCE_CONNECTION_NAME is None or self.DB_IAM_USER is None):
            msg = "DB_INSTANCE_CONNECTION_NAME and DB_IAM_USER must be set together"
            raise ValueError(msg)
        return self


database_settings = DatabaseSettings()
