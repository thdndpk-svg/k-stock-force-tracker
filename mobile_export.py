#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_web_app import analyze_payload


APP_DIR = Path(__file__).resolve().parent
DOCS_DIR = APP_DIR / "docs"


def build_mobile_payload() -> dict[str, object]:
    payload = analyze_payload()
    return {
        "generatedAt": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "count": payload.get("count", 0),
        "marketCsv": payload.get("marketCsv", ""),
        "searchMarketCsv": payload.get("searchMarketCsv", ""),
        "signals": payload.get("signals", {}),
        "bottomCandidates": payload.get("bottomCandidates", []),
        "sectors": payload.get("sectors", []),
        "items": payload.get("items", []),
        "searchItems": payload.get("searchItems", []),
    }


def main() -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    data = build_mobile_payload()
    (DOCS_DIR / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"mobile data saved: {DOCS_DIR / 'data.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
