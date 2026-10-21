"""Explicit configuration: live mode never silently falls back to a demo."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1"}


@dataclass(frozen=True)
class Settings:
    mode: str = "demo"
    db_path: str = "var/agent_workbench.db"
    seed_demo: bool = True
    cookie_secure: bool = False
    session_hours: int = 12
    base_url: str = "https://api.openai.com/v1"
    api_key: str = field(default="", repr=False)
    model: str = ""
    timeout: float = 25
    max_calls: int = 8
    max_output_tokens: int = 1500
    token_budget: int = 16000
    run_timeout: float = 120
    review_enabled: bool = True
    input_price: float | None = None
    output_price: float | None = None
    admin_username: str = ""
    admin_password: str = field(default="", repr=False)
    admin_tenant: str = "acme"

    def __post_init__(self):
        if self.mode not in {"demo", "live"}:
            raise ValueError("APP_MODE must be demo or live")
        if self.mode == "live" and (not self.api_key or not self.model):
            raise ValueError("live mode requires LLM_API_KEY and LLM_MODEL in .env")
        endpoint = urlsplit(self.base_url)
        if endpoint.scheme != "https" and not (
            endpoint.scheme == "http" and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("LLM_BASE_URL must use HTTPS or a local HTTP server")
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("LLM_BASE_URL cannot contain credentials, query, or fragment")
        if not 1 <= self.max_calls <= 20 or self.token_budget < 500:
            raise ValueError("invalid call or token budget")
        if not 1 <= self.timeout <= 120 or not 5 <= self.run_timeout <= 600:
            raise ValueError("invalid timeout")
        if not 1 <= self.session_hours <= 168 or not 100 <= self.max_output_tokens <= 16000:
            raise ValueError("invalid session lifetime or output limit")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path.cwd() / ".env", override=False)

        def price(name):
            return float(os.environ[name]) if os.getenv(name) else None

        mode = os.getenv("APP_MODE", "demo")
        return cls(
            mode=mode,
            db_path=os.getenv("APP_DB_PATH", "var/agent_workbench.db"),
            seed_demo=_flag("APP_SEED_DEMO", mode == "demo"),
            cookie_secure=_flag("APP_COOKIE_SECURE", False),
            session_hours=int(os.getenv("APP_SESSION_HOURS", "12")),
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "25")),
            max_calls=int(os.getenv("LLM_MAX_CALLS", "8")),
            max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1500")),
            token_budget=int(os.getenv("LLM_TOKEN_BUDGET", "16000")),
            run_timeout=float(os.getenv("LLM_RUN_TIMEOUT_SECONDS", "120")),
            review_enabled=_flag("LLM_REVIEW_ENABLED", True),
            input_price=price("LLM_INPUT_PRICE_PER_MILLION"),
            output_price=price("LLM_OUTPUT_PRICE_PER_MILLION"),
            admin_username=os.getenv("APP_ADMIN_USERNAME", ""),
            admin_password=os.getenv("APP_ADMIN_PASSWORD", ""),
            admin_tenant=os.getenv("APP_ADMIN_TENANT", "acme"),
        )
