from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    messaging_provider: str = "sendblue"
    messaging_inbound: str = "webhook"  # webhook | poll
    poll_interval_seconds: float = 2.0
    textbee_api_key: str = ""
    textbee_device_id: str = ""

    sendblue_api_key: str = ""
    sendblue_api_secret: str = ""
    sendblue_from_number: str = ""
    sendblue_webhook_secret: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"

    database_url: str = ""
    neon_api_key: str = ""
    neon_project_id: str = ""

    brightdata_api_token: str = ""
    brightdata_unlocker_zone: str = ""

    nominatim_user_agent: str = "care-navigator-hackathon"

    demo_phone: str = ""


settings = Settings()
