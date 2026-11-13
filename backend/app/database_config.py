from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PostgresDsn,
    PrivateAttr,
    StringConstraints,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
InstanceConnectionName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*:[a-z0-9-]+:[a-z][a-z0-9-]*$")
]


@dataclass(frozen=True, slots=True)
class DirectDatabaseConnection:
    url: PostgresDsn
    kind: Literal["direct"] = "direct"


@dataclass(frozen=True, slots=True)
class CloudSqlDatabaseConnection:
    instance: str
    iam_user: str
    database: str
    kind: Literal["cloud-sql-iam"] = "cloud-sql-iam"


type DatabaseConnection = DirectDatabaseConnection | CloudSqlDatabaseConnection


class CloudDatabaseSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    INSTANCE_CONNECTION_NAME: InstanceConnectionName | None = None
    IAM_USER: NonEmptyStr | None = None
    NAME: NonEmptyStr = "sarj_wiki"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="_",
        env_nested_max_split=1,
    )

    DATABASE_URL: PostgresDsn | None = Field(default=None, frozen=True)
    DB: CloudDatabaseSettings = Field(default_factory=CloudDatabaseSettings, frozen=True)
    _connection: DatabaseConnection = PrivateAttr()

    @property
    def connection(self) -> DatabaseConnection:
        return self._connection

    @model_validator(mode="after")
    def exactly_one_database_path(self) -> Self:
        dsn = self.DATABASE_URL is not None
        connector = self.DB.INSTANCE_CONNECTION_NAME is not None or self.DB.IAM_USER is not None
        if dsn == connector:
            msg = (
                "Configure exactly one database path: DATABASE_URL,"
                " or DB_INSTANCE_CONNECTION_NAME + DB_IAM_USER"
            )
            raise ValueError(msg)
        if connector and (self.DB.INSTANCE_CONNECTION_NAME is None or self.DB.IAM_USER is None):
            msg = "DB_INSTANCE_CONNECTION_NAME and DB_IAM_USER must be set together"
            raise ValueError(msg)
        if self.DATABASE_URL is not None:
            self._connection = DirectDatabaseConnection(self.DATABASE_URL)
        elif self.DB.INSTANCE_CONNECTION_NAME is not None and self.DB.IAM_USER is not None:
            self._connection = CloudSqlDatabaseConnection(
                self.DB.INSTANCE_CONNECTION_NAME, self.DB.IAM_USER, self.DB.NAME
            )
        return self


database_settings = DatabaseSettings()
