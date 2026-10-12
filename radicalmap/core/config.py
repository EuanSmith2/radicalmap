"""Runtime configuration. Environment overrides everything, so a scale run and
a polite smoke test are the same code with different settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # ── politeness ───────────────────────────────────────────────────────────
    # Default pacing per host. Deliberately conservative: the LEGAL.md claim to
    # operate as legitimate research rests partly on not behaving like an
    # attack, and a crawl that gets the collection IP banned produces no data
    # at all. robots.txt Crawl-delay, where present, always wins over this.
    requests_per_second: float = _float("RM_RPS", 0.5)
    burst: int = _int("RM_BURST", 3)
    respect_robots: bool = _bool("RM_RESPECT_ROBOTS", True)
    user_agent: str = os.environ.get(
        "RM_USER_AGENT",
        "RadicalMapResearchBot/2.0 (+https://github.com/EuanSmith2/radicalmap; "
        "academic research under GDPR Art. 89(1) / CDSM Art. 3)")

    # ── workers ──────────────────────────────────────────────────────────────
    workers: int = _int("RM_WORKERS", 4)
    job_batch: int = _int("RM_JOB_BATCH", 8)
    # Lease length bounds how long a crashed worker's jobs stay unavailable:
    # nothing can reclaim them until the lease expires. Sized at ~4x the request
    # timeout rather than a flat 600s, which the e2e run showed leaves work
    # stranded for ten minutes after a crash that took three seconds to happen.
    lease_seconds: int = _int("RM_LEASE_S", 180)
    max_attempts: int = _int("RM_MAX_ATTEMPTS", 5)
    request_timeout: float = _float("RM_TIMEOUT", 45.0)

    # ── circuit breaker ──────────────────────────────────────────────────────
    breaker_threshold: int = _int("RM_BREAKER_THRESHOLD", 8)
    breaker_cooldown: float = _float("RM_BREAKER_COOLDOWN", 300.0)

    # ── frontier ─────────────────────────────────────────────────────────────
    max_depth: int = _int("RM_MAX_DEPTH", 3)
    max_items: int = _int("RM_MAX_ITEMS", 0)          # 0 = unbounded
    expand_accounts: bool = _bool("RM_EXPAND_ACCOUNTS", True)
    expand_tags: bool = _bool("RM_EXPAND_TAGS", True)
    expand_sounds: bool = _bool("RM_EXPAND_SOUNDS", True)
    expand_commenters: bool = _bool("RM_EXPAND_COMMENTERS", False)

    # ── media ────────────────────────────────────────────────────────────────
    audio_dir: Path = Path(os.environ.get("RM_AUDIO_DIR", PROJECT_ROOT / "data" / "audio"))
    fetch_audio: bool = _bool("RM_FETCH_AUDIO", False)
    cookies_from_browser: str = os.environ.get("RM_COOKIES_BROWSER", "")

    # ── safety ───────────────────────────────────────────────────────────────
    # Network access is OFF unless explicitly enabled. Collection is an
    # outward-facing act against a live platform; it should never start as a
    # side effect of running something else.
    allow_network: bool = _bool("RM_ALLOW_NETWORK", False)
    dry_run: bool = _bool("RM_DRY_RUN", False)


settings = Settings()
