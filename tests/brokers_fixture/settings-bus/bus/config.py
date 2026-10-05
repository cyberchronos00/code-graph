"""Settings with snake_case field defaults (#157)."""
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    events_topic: str = "events"
    jobs_queue: str = "jobs"
    audit_topic: str = Field(default="audit.orders")


settings = Settings()
