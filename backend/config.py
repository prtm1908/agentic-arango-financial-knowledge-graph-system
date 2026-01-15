import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    arango_url: str = os.getenv("ARANGO_URL", "http://localhost:8529")
    arango_db: str = os.getenv("ARANGO_DB", "financial_kg")
    arango_username: str = os.getenv("ARANGO_USERNAME", "root")
    arango_password: str = os.getenv("ARANGO_PASSWORD", "")
    arango_seed_data: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
