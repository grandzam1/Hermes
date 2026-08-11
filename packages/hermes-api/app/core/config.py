import importlib.metadata
import json
import os
import re
from typing import Any, Optional

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import DotEnvSettingsSource, EnvSettingsSource


def _parse_list_field(field_name: str, value: Any) -> Any | None:
    """Parse flexible list env values; return None to use default handling."""
    LIST_FIELDS = {"allowed_origins", "api_keys"}
    if field_name not in LIST_FIELDS or not isinstance(value, str):
        return None
    if not value.strip():
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [item.strip() for item in parsed if item.strip()]
    except (json.JSONDecodeError, ValueError):
        pass
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return [item.strip() for item in value.split() if item.strip()]


class CustomEnvSettingsSource(EnvSettingsSource):
    """Custom environment settings source that handles list fields robustly."""

    def prepare_field_value(
        self, field_name: str, field: Any, value: Any, value_is_complex: bool
    ) -> Any:
        parsed = _parse_list_field(field_name, value)
        if parsed is not None:
            return parsed
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class CustomDotEnvSettingsSource(DotEnvSettingsSource):
    """Dotenv source with the same flexible list parsing as CustomEnvSettingsSource."""

    def prepare_field_value(
        self, field_name: str, field: Any, value: Any, value_is_complex: bool
    ) -> Any:
        parsed = _parse_list_field(field_name, value)
        if parsed is not None:
            return parsed
        return super().prepare_field_value(field_name, field, value, value_is_complex)


class Settings(BaseSettings):
    # API Settings
    api_title: str = Field(default="Hermes API")
    api_description: str = Field(default="Video downloader API")

    @property
    def api_version(self) -> str:
        """Get version from CI build metadata, package metadata, or pyproject.toml."""
        build_version = os.getenv("HERMES_BUILD_VERSION", "").strip()
        if build_version:
            return build_version

        # Try to get from installed package metadata first
        try:
            return importlib.metadata.version("hermes-api")
        except importlib.metadata.PackageNotFoundError:
            pass

        # Try to read from pyproject.toml (development)
        try:
            import pathlib

            pyproject_path = (
                pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
            )
            if pyproject_path.exists():
                with open(pyproject_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # Extract version using regex
                match = re.search(
                    r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE
                )
                if match:
                    return match.group(1)
        except (FileNotFoundError, OSError):
            pass

        # Final fallback
        return "1.0.0"

    debug: bool = Field(default=False)

    # Database
    database_url: str = Field(default="sqlite+aiosqlite:///./data/hermes.db")
    database_echo: bool = Field(default=False)

    # Redis/Cache
    redis_url: str = Field(default="redis://localhost:6379")
    redis_db: int = Field(default=0)

    # Security
    secret_key: str = Field(
        ...,
        description="JWT secret key for token signing. Must be set via HERMES_SECRET_KEY environment variable.",
    )
    algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=1440)  # 24 hours for testing
    refresh_token_expire_days: int = Field(default=30)  # 30 days for testing

    # File Storage
    download_dir: str = Field(default="./downloads")
    temp_dir: str = Field(default="./temp")

    # Optional browser cookies JSON (Chrome/Edge export). Converted to Netscape for yt-dlp.
    # Single file or comma-separated list, e.g. ./cookies/youtube.json,./cookies/tiktok.json
    cookies_json: Optional[str] = Field(
        default=None,
        description="Path(s) to browser-exported JSON cookies used by yt-dlp",
    )

    # Browser impersonation via curl_cffi (opt-in; TikTok-only by default to avoid
    # changing YouTube/other extractors that already work).
    # Set empty / "off" to disable. Example target: chrome
    ytdlp_impersonate: Optional[str] = Field(
        default="chrome",
        description="yt-dlp impersonate target (e.g. chrome). Empty/off disables.",
    )
    # Comma-separated host fragments, or "all". Default: tiktok only.
    ytdlp_impersonate_sites: str = Field(
        default="tiktok",
        description="Sites that may use impersonation: 'tiktok', 'all', or host fragments",
    )
    # Short UA that helps TikTok challenge pages on some datacenter IPs.
    # Applied only to ytdlp_impersonate_sites (TikTok by default). Empty/off disables.
    ytdlp_user_agent: Optional[str] = Field(
        default="Mozilla/5.0",
        description="Optional User-Agent override for selected sites (TikTok by default)",
    )

    # API Keys
    api_keys: list[str] = Field(default_factory=list)

    # Rate Limiting
    rate_limit_per_minute: int = Field(default=60)

    # Security Settings
    enable_token_blacklist: bool = Field(default=True)
    enable_rate_limiting: bool = Field(default=True)
    max_login_attempts: int = Field(default=5)
    login_attempt_window_minutes: int = Field(default=15)

    # Signup Control Settings
    allow_public_signup: bool = Field(
        default=True,
        description="Allow public user registration. When False, only admins can create users.",
    )
    initial_admin_username: Optional[str] = Field(
        default=None,
        description="Username for initial admin account (created on first startup if no users exist)",
    )
    initial_admin_email: Optional[str] = Field(
        default=None,
        description="Email for initial admin account (created on first startup if no users exist)",
    )
    initial_admin_password: Optional[str] = Field(
        default=None,
        description="Password for initial admin account (created on first startup if no users exist)",
    )

    # CORS Settings
    # Default includes common development ports and example domains
    # In production, override with HERMES_ALLOWED_ORIGINS environment variable
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",  # Production frontend (nginx)
            "http://localhost:5173",  # Development frontend (Vite)
            "https://hermes.example.com",  # Example production domain
            "https://hermes-api.example.com",  # Example separate API domain
        ]
    )
    allow_credentials: bool = Field(default=True)

    # =============================================================================
    # SERVER-SENT EVENTS (SSE) CONFIGURATION
    # =============================================================================

    sse_heartbeat_interval: int = Field(
        default=30, description="SSE heartbeat interval in seconds"
    )
    sse_max_connections: int = Field(
        default=1000, description="Maximum concurrent SSE connections"
    )
    sse_connection_timeout: int = Field(
        default=300, description="SSE connection timeout in seconds"
    )

    model_config = SettingsConfigDict(
        env_prefix="HERMES_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use custom environment settings source for robust list field parsing."""
        return (
            init_settings,
            CustomEnvSettingsSource(settings_cls),
            CustomDotEnvSettingsSource(settings_cls),
            file_secret_settings,
        )


# Global settings instance
settings = Settings()
