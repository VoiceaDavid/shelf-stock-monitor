"""Settings, read from environment variables / the .env file (never committed)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://shelf:shelf@db:5432/shelf"

    # MQTT broker (TLS)
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 8883
    mqtt_username: str = "cloud"
    mqtt_password: str = ""
    mqtt_ca_file: str = "/certs/ca.crt"
    mqtt_enabled: bool = True

    # Shopify (Dev Dashboard app, client credentials grant)
    shopify_shop: str = ""  # e.g. shelf-stock-demo.myshopify.com
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    shopify_api_version: str = "2026-10"
    shopify_location_id: str = ""  # optional; first location is used if empty
    shopify_dry_run: bool = False  # True = log instead of calling Shopify


@lru_cache
def get_settings() -> Settings:
    return Settings()
