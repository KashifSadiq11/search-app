from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass

ROOT_DIR = Path(__file__).resolve().parents[2]
ROOT_ENV = ROOT_DIR / ".env"


def _load_root_env() -> None:
    """
    Manually load key=value pairs from root .env without extra deps.

    Lines beginning with `#` are ignored.
    Values already present in os.environ are not overridden.
    """
    if not ROOT_ENV.exists():
        return

    try:
        content = ROOT_ENV.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            # Don't override an explicitly exported environment variable
            if key not in os.environ:
                os.environ[key] = value
    except Exception:
        # Fail silently; explicit env vars or defaults will be used instead.
        pass


def _normalize_host(url: str) -> str:
    """
    Replace '@db' with '@localhost' only if clearly outside a container.
    This allows using the same DATABASE_URL in Docker and on the host.
    """
    if "@db" in url and not Path("/.dockerenv").exists():
        return url.replace("@db", "@localhost")
    return url


_load_root_env()


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_echo: bool
    CURRENT_ENVIRONMENT: str  # required, no default

    @staticmethod
    def load() -> "Settings":
        raw_url = os.getenv("DATABASE_URL")
        raw_echo = os.getenv("DATABASE_ECHO")
        raw_env = os.getenv("CURRENT_ENVIRONMENT")

        if not raw_url:
            raise RuntimeError(
                "DATABASE_URL is required but not set. "
                "Ensure it exists in the project root .env file (rec-engine/.env)."
            )

        if not raw_env:
            raise RuntimeError(
                "CURRENT_ENVIRONMENT is required but not set. "
                "Please define it in your .env file (e.g. CURRENT_ENVIRONMENT=development)."
            )

        # Default echo to false if not provided
        if raw_echo is None:
            raw_echo = "false"

        # Normalize hostname for local runs
        url = _normalize_host(raw_url)
        echo = str(raw_echo).lower() in {"1", "true", "yes", "on"}
        env = raw_env.strip().lower()

        return Settings(
            database_url=url,
            database_echo=echo,
            CURRENT_ENVIRONMENT=env,
        )


settings = Settings.load()
