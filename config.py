from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
REPORT_DIR = APP_DIR / "reports"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or (APP_DIR / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class KisConfig:
    app_key: str
    app_secret: str
    account_no: str = ""
    product_code: str = "01"
    virtual: bool = True

    @property
    def base_url(self) -> str:
        if self.virtual:
            return "https://openapivts.koreainvestment.com:29443"
        return "https://openapi.koreainvestment.com:9443"


@dataclass(frozen=True)
class DartConfig:
    api_key: str


def get_kis_config() -> KisConfig | None:
    load_dotenv()
    app_key = os.environ.get("KIS_APP_KEY", "").strip()
    app_secret = os.environ.get("KIS_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        return None
    return KisConfig(
        app_key=app_key,
        app_secret=app_secret,
        account_no=os.environ.get("KIS_ACCOUNT_NO", "").strip(),
        product_code=os.environ.get("KIS_PRODUCT_CODE", "01").strip() or "01",
        virtual=os.environ.get("KIS_VIRTUAL", "true").lower() in {"1", "true", "yes", "y"},
    )


def get_dart_config() -> DartConfig | None:
    load_dotenv()
    api_key = os.environ.get("DART_API_KEY", "").strip()
    if not api_key:
        return None
    return DartConfig(api_key=api_key)
