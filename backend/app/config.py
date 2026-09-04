import secrets

from pydantic import Field
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
    # How stale an inbound webhook may be before it is rejected as a replay.
    hunar_webhook_skew_seconds: int = 300

    # The key the demo-mode simulator signs its synthetic webhooks with. Nothing
    # to do with any Hunar-issued credential; we generate it. Randomised per
    # process so a fresh clone runs with no setup and there is no signing key
    # literal in the source. Set it explicitly to keep it stable across restarts.
    demo_webhook_signing_key: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32)
    )

    internal_api_token: str = ""
    app_shared_secret: str = ""

    people_search_provider: str = "fixture"
    pdl_api_key: str = ""

    gemini_api_key: str = ""

    notification_channel: str = "logged"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""

    def webhook_signing_key(self) -> tuple[str, str]:
        """The key inbound webhooks are verified against, and the env var it came
        from so a misconfiguration can name itself in the logs.

        Demo mode verifies against our own generated key rather than skipping
        verification, so the deployed demo exercises the real HMAC path instead
        of a bypass that only gets tested in production.

        Live mode uses the Hunar API key, which defaults to empty. An empty key
        would make `verify_signature` an HMAC keyed on "", which anyone could
        forge; the caller turns that into a 503 rather than trusting it.
        """
        if self.demo_mode:
            return self.demo_webhook_signing_key, "DEMO_WEBHOOK_SIGNING_KEY"
        return self.hunar_api_key, "HUNAR_API_KEY"


settings = Settings()  # type: ignore[call-arg]
