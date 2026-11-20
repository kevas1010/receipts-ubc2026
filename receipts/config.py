"""Every rule and integration switch lives in .env so the demo window and the real month share one code path."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # dotenv is optional; plain env vars work too
    pass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("RECEIPTS_DB", str(Path(__file__).resolve().parent.parent / "receipts.db")))

    # Verification rules (defaults = the real one-month badge)
    window_days: int = field(default_factory=lambda: _int("VERIFY_WINDOW_DAYS", 30))
    min_picks: int = field(default_factory=lambda: _int("VERIFY_MIN_PICKS", 15))
    min_active_days: int = field(default_factory=lambda: _int("VERIFY_MIN_DAYS", 10))
    max_edits: int = field(default_factory=lambda: _int("VERIFY_MAX_EDITS", 0))
    max_units: float = field(default_factory=lambda: _float("MAX_UNITS", 10))
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", False))
    # Verified analysts carry a PickProof credential and post through PickProof (hash-committed picks), so the
    # consumer scraper leaves their new Discord posts alone. Set true to keep grading them from Discord too.
    scrape_verified: bool = field(default_factory=lambda: _bool("SCRAPE_VERIFIED", False))

    # Grading
    close_snapshot_minutes: int = field(default_factory=lambda: _int("CLOSE_SNAPSHOT_MINUTES", 10))
    void_after_days: int = field(default_factory=lambda: _int("VOID_AFTER_DAYS", 4))

    # Channels the bot watches
    channel_keywords: tuple[str, ...] = field(default_factory=lambda: tuple(
        k.strip().lower() for k in os.getenv("PICK_CHANNEL_KEYWORDS", "moneyline,ml-picks").split(",") if k.strip()))

    # Gemini (public, no key)
    gemini_base: str = field(default_factory=lambda: os.getenv("GEMINI_API_BASE", "https://api.gemini.com"))
    gemini_market_url: str = field(default_factory=lambda: os.getenv(
        "GEMINI_MARKET_URL", "https://www.gemini.com/predictions/{slug}"))

    # Other venues (public, no key); price comparison only, never grading
    show_other_venues: bool = field(default_factory=lambda: _bool("SHOW_OTHER_VENUES", True))
    kalshi_base: str = field(default_factory=lambda: os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2"))
    polymarket_base: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_BASE", "https://gamma-api.polymarket.com"))

    # Arc testnet (free faucet). Without a key, picks lock "unanchored" with the hash stored locally.
    arc_rpc: str = field(default_factory=lambda: os.getenv("ARC_RPC_URL", "https://rpc.testnet.arc.network"))
    arc_chain_id: int = field(default_factory=lambda: _int("ARC_CHAIN_ID", 5042002))
    arc_private_key: str = field(default_factory=lambda: os.getenv("ARC_PRIVATE_KEY", ""))
    arc_contract: str = field(default_factory=lambda: os.getenv("PICK_REGISTRY_ADDRESS", ""))
    arc_explorer: str = field(default_factory=lambda: os.getenv("ARC_EXPLORER_URL", "https://testnet.arcscan.app"))

    # Discord
    discord_token: str = field(default_factory=lambda: os.getenv("DISCORD_TOKEN", ""))
    verified_role: str = field(default_factory=lambda: os.getenv("VERIFIED_ROLE_NAME", "Verified Capper"))
    public_url: str = field(default_factory=lambda: os.getenv("PUBLIC_URL", "http://localhost:8000"))

    @property
    def chain_enabled(self) -> bool:
        return bool(self.arc_private_key and self.arc_contract)


settings = Settings()
