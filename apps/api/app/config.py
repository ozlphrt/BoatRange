"""Application configuration."""

from pydantic import BaseModel


class Settings(BaseModel):
    """Application settings loaded from environment or defaults."""

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "axopar_dev"
    postgres_user: str = "axopar"
    postgres_password: str = "axopar_dev_password"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
