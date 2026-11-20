from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


@dataclass
class Settings:
    arena_url: str = field(default_factory=lambda: os.getenv("ARENA_URL", "http://localhost:8080"))
    arena_token_path: str = field(default_factory=lambda: os.getenv("ARENA_TOKEN_PATH", "/oauth/v2/token"))
    arena_client_id: str = field(default_factory=lambda: os.getenv("ARENA_CLIENT_ID", ""))
    arena_client_secret: str = field(default_factory=lambda: os.getenv("ARENA_CLIENT_SECRET", ""))
    arena_api_token: str = field(default_factory=lambda: os.getenv("ARENA_API_TOKEN", ""))
    arena_verify_tls: bool = field(default_factory=lambda: _bool("ARENA_VERIFY_TLS", True))
    # Empty -> take /arena/current-event
    arena_event_id: str = field(default_factory=lambda: os.getenv("ARENA_EVENT_ID", ""))
    # Restrict to these mats (by name or id); empty -> all mats of the event
    mats: list[str] = field(default_factory=lambda: _list("MATS"))
    poll_interval: float = field(default_factory=lambda: float(os.getenv("POLL_INTERVAL", "1.0")))
    mats_refresh: float = field(default_factory=lambda: float(os.getenv("MATS_REFRESH", "60")))
    http_timeout: float = field(default_factory=lambda: float(os.getenv("HTTP_TIMEOUT", "5")))
    # Path to a JSON file overriding app/mapping.default.json
    mapping_file: str = field(default_factory=lambda: os.getenv("MAPPING_FILE", ""))
    # seconds | ms — unit of a numeric timer value coming from Arena
    timer_unit: str = field(default_factory=lambda: os.getenv("TIMER_UNIT", "s"))
    # Prefix of /vmix columns: name (A_red_name) | index (mat1_red_name)
    vmix_key: str = field(default_factory=lambda: os.getenv("VMIX_KEY", "name"))
    # Enable /raw passthrough for inspecting real Arena responses
    enable_raw: bool = field(default_factory=lambda: _bool("ENABLE_RAW", True))
