from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every environment variable the backend reads, in one place.

    Only `database_url` is required. Everything else has a default that keeps the
    app bootable, because the deployed demo must come up even with no vendor keys
    configured at all.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    frontend_origin: str = "http://localhost:3000"
    public_base_url: str = "http://localhost:8000"

    # false makes the app place real, billable phone calls. See section 8 of notes.
    demo_mode: bool = True

    hunar_api_key: str = ""
    hunar_base_url: str = "https://api.voice.hunar.ai/external/v1"
    hunar_webhook_secret: str = ""

    internal_api_token: str = ""
    app_shared_secret: str = ""

    people_search_provider: str = "fixture"
    pdl_api_key: str = ""

    gemini_api_key: str = ""

    notification_channel: str = "logged"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""


settings = Settings()  # type: ignore[call-arg]
