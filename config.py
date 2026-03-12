import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    discord_token: str
    api_base_url: str

    imap_host: str
    imap_user: str
    imap_password: str
    imap_port: int
    imap_poll_interval: int

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str

    database_path: str
    rate_limit_max: int
    rate_limit_window: int
    encryption_key: bytes

    @property
    def email_enabled(self) -> bool:
        return bool(self.imap_host and self.smtp_host)


def load_config() -> Config:
    enc_key = os.environ.get("ENCRYPTION_KEY", "")
    if not enc_key:
        raise RuntimeError(
            "ENCRYPTION_KEY is required. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Config(
        discord_token=os.environ["DISCORD_BOT_TOKEN"],
        api_base_url=os.environ.get("API_BASE_URL", "https://tapahtumat.vihreaturku.fi"),

        imap_host=os.environ.get("IMAP_HOST", ""),
        imap_user=os.environ.get("IMAP_USER", ""),
        imap_password=os.environ.get("IMAP_PASSWORD", ""),
        imap_port=int(os.environ.get("IMAP_PORT", "993")),
        imap_poll_interval=int(os.environ.get("IMAP_POLL_INTERVAL_SECONDS", "60")),

        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("SMTP_FROM", ""),

        database_path=os.environ.get("DATABASE_PATH", "audit.db"),
        rate_limit_max=int(os.environ.get("RATE_LIMIT_MAX", "10")),
        rate_limit_window=int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "3600")),
        encryption_key=enc_key.encode(),
    )
