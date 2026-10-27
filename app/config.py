"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://postgres:postgres@localhost:5432/identity"
    # Secret used to HMAC every match/blocking key before it is stored. Keys in
    # the database are therefore pseudonymous. Changing this value invalidates
    # every stored key, so treat it like an encryption key and rotate with a
    # full re-key job.
    key_secret: str = "change-me"
    # Comma-separated API keys accepted in the X-API-Key header. Empty disables auth.
    api_keys: frozenset[str] = field(default_factory=frozenset)
    # Blocking keys that already point at more records than this are skipped
    # when building the candidate pool (e.g. a student hall's postcode).
    max_block_size: int = 5000
    # Hard ceiling on the candidate pool for a single inbound record.
    max_candidates: int = 2000
    # Maximum records accepted by the batch endpoint.
    max_batch_size: int = 500
    # Apply SQL migrations on start-up.
    auto_migrate: bool = True
    # Tiers whose matches are linked automatically. A match in any other tier
    # creates a new customer and is queued in merge_candidate for review.
    auto_link_tiers: frozenset[str] = frozenset({"exact", "strong", "probable", "possible"})
    # Send the review app's session cookie only over HTTPS. Browsers treat
    # http://localhost as secure, so local development still works.
    review_cookie_secure: bool = True
    # Demo only: after a decision, show the known answer for synthetic cases.
    demo_mode: bool = False
    pool_min_size: int = 1
    pool_max_size: int = 10
    log_level: str = "INFO"

    @staticmethod
    def from_env() -> "Settings":
        keys = os.getenv("API_KEYS", "")
        return Settings(
            database_url=os.getenv("DATABASE_URL", Settings.database_url),
            key_secret=os.getenv("KEY_SECRET", Settings.key_secret),
            api_keys=frozenset(k.strip() for k in keys.split(",") if k.strip()),
            max_block_size=_int("MAX_BLOCK_SIZE", Settings.max_block_size),
            max_candidates=_int("MAX_CANDIDATES", Settings.max_candidates),
            max_batch_size=_int("MAX_BATCH_SIZE", Settings.max_batch_size),
            auto_migrate=_bool("AUTO_MIGRATE", Settings.auto_migrate),
            auto_link_tiers=frozenset(
                t.strip() for t in os.getenv("AUTO_LINK_TIERS", "exact,strong,probable,possible").split(",") if t.strip()
            ),
            review_cookie_secure=_bool("REVIEW_COOKIE_SECURE", Settings.review_cookie_secure),
            demo_mode=_bool("DEMO_MODE", Settings.demo_mode),
            pool_min_size=_int("DB_POOL_MIN", Settings.pool_min_size),
            pool_max_size=_int("DB_POOL_MAX", Settings.pool_max_size),
            log_level=os.getenv("LOG_LEVEL", Settings.log_level),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
