"""Broker addresses as class-field defaults (#158)."""


class Settings:
    kafka_url: str = "redpanda:9092"
    amqp_url: str = "amqp://app:s3cret-kafka@rabbitmq:5672/shop"


settings = Settings()
