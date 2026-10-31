"""Runtime settings, read from environment variables."""
import os
from dataclasses import dataclass


def _f(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: str) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql://pulse:pulse@localhost:5432/pulse")
    admin_key: str = os.getenv("ADMIN_KEY", "change-me-admin-key")
    keys_file: str = os.getenv("KEYS_FILE", "data/api_keys.json")
    # Worker
    poll_interval: float = _f("POLL_INTERVAL", "5")          # seconds between polling cycles
    batch_size: int = _i("BATCH_SIZE", "5000")               # changed orders per transaction
    overlap_seconds: float = _f("OVERLAP_SECONDS", "10")     # re-read window, protects against late commits
    # API
    pool_max: int = _i("POOL_MAX", "20")
    max_concurrent_per_tenant: int = _i("MAX_CONCURRENT_PER_TENANT", "4")


settings = Settings()
