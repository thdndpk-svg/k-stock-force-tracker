#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import time
import webbrowser
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import DATA_DIR, REPORT_DIR, get_kis_config
from csv_utils import parse_date, write_csv
from data_loader import discover_investor_csvs, discover_market_csv, load_history_dir, load_market_snapshots, merge_krx_investor_csvs
from global_signals import load_global_signals
from kis_client import KisApiError, KisClient, save_json
from kis_supply import aggregate_supply_rows, load_market_csv_rows, market_rows_from_supply_rows, market_rows_from_volume_rank, merge_market_rows, save_market_csv, save_supply_csv
from krx_downloader import fetch_krx_bundle
from scoring import ForceTracker


DEFAULT_MARKET_CSV = DATA_DIR / "sample_market.csv"
DEFAULT_HISTORY_DIR = DATA_DIR / "history"
DEFAULT_INVESTOR_CSV = DATA_DIR / "krx" / "investor_net_buy.csv"


def run_analyze(args: argparse.Namespace) -> int:
    market_csv = Path(args.market_csv)
    if not market_csv.exists() and args.market_csv == str(DEFAULT_MARKET_CSV):
        market_csv = discover_market_csv(DATA_DIR)
    investor_paths = [Path(args.investor_csv)] if Path(args.investor_csv).exists() else []
    if not investor_paths and args.investor_csv == str(DEFAULT_INVESTOR_CSV):
        investor_paths = discover_investor_csvs(DATA_DIR)
    print(f"market csv: {market_csv}")
    print(f"investor csv: {', '.join(str(path) for path in investor_paths) or '없음'}")
    snapshots = load_market_snapshots(market_csv)
    snapshots = merge_krx_investor_csvs(snapshots, investor_paths) if investor_paths else snapshots
    history = load_history_dir(Path(args.history_dir))
    signals = load_global_signals().get("signals", {})
    results = ForceTracker(snapshots, history, signals).score_all(limit=args.limit)
    rows = [
        {
            "rank": idx,
            "grade": item.grade,
            "recommendation": item.recommendation,
            "trade_action": item.trade_action,
            "trade_reason": item.trade_reason,
            "risk": item.risk_label,
            "score": item.score,
            "discovery_score": item.discovery_score,
            "code": item.code,
            "name": item.name,
            "sector": item.sector,
            "theme": item.theme,
            "market": item.market,
            "close": int(item.close),
            "change_rate": round(item.change_rate, 2),
            "trading_value": int(item.trading_value),
            "volume_ratio": item.volume_ratio,
            "flow_ratio": item.flow_ratio,
            "us_impact": item.us_impact,
            "bottom_score": item.bottom_score,
            "bottom_stage": item.bottom_stage,
            "bottom_support": int(item.bottom_support),
            "bottom_long_line": int(item.bottom_long_line),
            "bottom_touch_count": item.bottom_touch_count,
            "bottom_reasons": " / ".join(item.bottom_reasons),
            "bottom_warnings": " / ".join(item.bottom_warnings),
            "foreign_net_value": int(item.foreign_net_value),
            "institution_net_value": int(item.institution_net_value) if item.institution_net_available else "",
            "tags": ", ".join(item.tags),
            "reasons": " / ".join(item.reasons),
            "penalties": " / ".join(item.penalties),
        }
        for idx, item in enumerate(results, 1)
    ]
    output = Path(args.output)
    write_csv(
        output,
        rows,
        [
            "rank",
            "grade",
            "recommendation",
            "trade_action",
            "trade_reason",
            "risk",
            "score",
            "discovery_score",
            "code",
            "name",
            "sector",
            "theme",
            "market",
            "close",
            "change_rate",
            "trading_value",
            "volume_ratio",
            "flow_ratio",
            "us_impact",
            "bottom_score",
            "bottom_stage",
            "bottom_support",
            "bottom_long_line",
            "bottom_touch_count",
            "bottom_reasons",
            "bottom_warnings",
            "foreign_net_value",
            "institution_net_value",
            "tags",
            "reasons",
            "penalties",
        ],
    )
    for row in rows[:10]:
        print(
            f"{row['rank']:02d}. {row['name']}({row['code']}) "
            f"{row['score']}점 {row['recommendation']} {row['tags']}"
        )
    print(f"\nReport saved: {output}")
    return 0


def run_fetch_kis(args: argparse.Namespace) -> int:
    config = get_kis_config()
    if config is None:
        raise SystemExit("KIS_APP_KEY/KIS_APP_SECRET 환경변수 또는 .env 설정이 필요합니다.")
    client = KisClient(config)
    codes = [code.strip().zfill(6) for code in args.codes.split(",") if code.strip()]
    output_dir = Path(args.output_dir)
    for code in codes:
        price = with_retry(lambda code=code: client.inquire_price(code), retries=args.retries)
        save_json(output_dir / f"{code}_price.json", price)
        print(f"saved {code} price")
        time.sleep(args.delay)
    if args.volume_rank:
        time.sleep(args.delay)
        save_json(output_dir / "volume_rank.json", with_retry(client.volume_rank, retries=args.retries))
        print("saved volume_rank")
    return 0


def run_fetch_kis_supply(args: argparse.Namespace) -> int:
    config = get_kis_config()
    if config is None:
        raise SystemExit("KIS_APP_KEY/KIS_APP_SECRET 환경변수 또는 .env 설정이 필요합니다.")
    client = KisClient(config)
    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)

    market_codes = [code.strip() for code in args.market_codes.split(",") if code.strip()]
    foreign_rows = []
    institution_rows = []
    for market_code in market_codes:
        foreign_payload = with_retry(
            lambda market_code=market_code: client.foreign_institution_total(investor="1", market_code=market_code),
            retries=args.retries,
        )
        save_json(raw_dir / f"foreign_institution_foreign_{market_code}.json", foreign_payload)
        foreign_rows.extend(aggregate_supply_rows(foreign_payload, "foreign"))
        time.sleep(args.delay)

        institution_payload = with_retry(
            lambda market_code=market_code: client.foreign_institution_total(investor="2", market_code=market_code),
            retries=args.retries,
        )
        save_json(raw_dir / f"foreign_institution_institution_{market_code}.json", institution_payload)
        institution_rows.extend(aggregate_supply_rows(institution_payload, "institution"))
        time.sleep(args.delay)

    volume_payload = with_retry(client.volume_rank, retries=args.retries)
    save_json(raw_dir / "volume_rank.json", volume_payload)
    volume_rows = market_rows_from_volume_rank(volume_payload)

    foreign_path = save_supply_csv(output_dir / "kis_외국인_순매수.csv", foreign_rows)
    institution_path = save_supply_csv(output_dir / "kis_기관_순매수.csv", institution_rows)
    market_path = save_market_csv(DATA_DIR / "kis_market.csv", merge_market_rows(market_rows_from_supply_rows(foreign_rows + institution_rows) + volume_rows))

    print(f"saved KIS foreign net-buy: {foreign_path} ({len(foreign_rows)} rows)")
    print(f"saved KIS institution net-buy: {institution_path} ({len(institution_rows)} rows)")
    print(f"saved KIS market snapshot: {market_path} ({len(foreign_rows + institution_rows + volume_rows)} source rows)")
    return 0


def _raw_payload_files(raw_dir: Path, prefix: str) -> list[Path]:
    specific = sorted(raw_dir.glob(f"{prefix}_*.json"))
    if specific:
        return specific
    plain = raw_dir / f"{prefix}.json"
    return [plain] if plain.exists() else []


def _infer_kis_trade_date(date_text: str | None) -> date:
    if date_text:
        return parse_date(date_text)
    for row in load_market_csv_rows(DATA_DIR / "kis_market.csv"):
        row_date = str(row.get("date") or "").strip()
        if row_date:
            return parse_date(row_date)
    return date.today()


def run_rebuild_kis_cache(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir)
    output_dir = Path(args.output_dir)
    trade_date = _infer_kis_trade_date(args.date)

    foreign_rows = []
    for path in _raw_payload_files(raw_dir, "foreign_institution_foreign"):
        foreign_rows.extend(aggregate_supply_rows(json.loads(path.read_text(encoding="utf-8")), "foreign", trade_date=trade_date))

    institution_rows = []
    for path in _raw_payload_files(raw_dir, "foreign_institution_institution"):
        institution_rows.extend(aggregate_supply_rows(json.loads(path.read_text(encoding="utf-8")), "institution", trade_date=trade_date))

    volume_path = raw_dir / "volume_rank.json"
    volume_rows = []
    if volume_path.exists():
        volume_payload = json.loads(volume_path.read_text(encoding="utf-8"))
        volume_rows = market_rows_from_volume_rank(volume_payload, trade_date=trade_date)

    foreign_path = save_supply_csv(output_dir / "kis_외국인_순매수.csv", foreign_rows)
    institution_path = save_supply_csv(output_dir / "kis_기관_순매수.csv", institution_rows)
    market_path = save_market_csv(
        DATA_DIR / "kis_market.csv",
        merge_market_rows(market_rows_from_supply_rows(foreign_rows + institution_rows, trade_date=trade_date) + volume_rows),
    )

    print(f"rebuilt KIS foreign net-buy: {foreign_path} ({len(foreign_rows)} rows)")
    print(f"rebuilt KIS institution net-buy: {institution_path} ({len(institution_rows)} rows)")
    print(f"rebuilt KIS market snapshot: {market_path} ({len(foreign_rows + institution_rows + volume_rows)} source rows)")
    return 0


def run_fetch_krx(args: argparse.Namespace) -> int:
    market_path, investor_path = fetch_krx_bundle(args.date, DATA_DIR)
    print(f"saved market csv: {market_path}")
    print(f"saved foreign net-buy csv: {investor_path}")
    return 0


def with_retry(call, retries: int = 3):
    for attempt in range(retries + 1):
        try:
            return call()
        except KisApiError as error:
            text = str(error)
            if "EGW00201" not in text or attempt >= retries:
                raise
            wait = 1.2 + attempt * 0.8
            print(f"KIS rate limit, retrying in {wait:.1f}s")
            time.sleep(wait)


def run_serve(args: argparse.Namespace) -> int:
    from stock_web_app import run_server

    run_server(args.host, args.port)
    return 0


def guess_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def run_serve_mobile(args: argparse.Namespace) -> int:
    if args.refresh_data:
        from mobile_export import main as export_mobile

        export_mobile()

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    docs_dir = DATA_DIR.parent / "docs"
    handler = partial(QuietHandler, directory=str(docs_dir))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    local_url = f"http://127.0.0.1:{args.port}/"
    phone_url = f"http://{guess_lan_ip()}:{args.port}/"
    print(f"Mobile stock viewer: {phone_url}")
    print("휴대폰과 이 Mac이 같은 Wi-Fi에 있어야 열립니다.")
    print(f"Mac local preview: {local_url}")
    if args.open:
        webbrowser.open(local_url)
    server.serve_forever()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Korea stock force-buy tracker.")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="Analyze CSV/KRX data and rank candidates.")
    analyze.add_argument("--market-csv", default=str(DEFAULT_MARKET_CSV))
    analyze.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    analyze.add_argument("--investor-csv", default=str(DEFAULT_INVESTOR_CSV))
    analyze.add_argument("--limit", type=int, default=30)
    analyze.add_argument("--output", default=str(REPORT_DIR / "force_rank.csv"))
    analyze.set_defaults(func=run_analyze)

    fetch = sub.add_parser("fetch-kis", help="Fetch selected KIS data as raw JSON.")
    fetch.add_argument("--codes", required=True, help="Comma separated stock codes, e.g. 005930,000660")
    fetch.add_argument("--volume-rank", action="store_true")
    fetch.add_argument("--output-dir", default=str(DATA_DIR / "kis_raw"))
    fetch.add_argument("--delay", type=float, default=0.25, help="Delay between KIS API calls.")
    fetch.add_argument("--retries", type=int, default=4, help="Retry count for KIS rate limit.")
    fetch.set_defaults(func=run_fetch_kis)

    kis_supply = sub.add_parser("fetch-kis-supply", help="Fetch KIS foreign/institution net-buy rankings.")
    kis_supply.add_argument("--market-codes", default="0000,0001,1001", help="Comma separated: 0000 all, 0001 KOSPI, 1001 KOSDAQ.")
    kis_supply.add_argument("--raw-dir", default=str(DATA_DIR / "kis_raw"))
    kis_supply.add_argument("--output-dir", default=str(DATA_DIR / "krx"))
    kis_supply.add_argument("--delay", type=float, default=1.2, help="Delay between KIS API calls.")
    kis_supply.add_argument("--retries", type=int, default=4, help="Retry count for KIS rate limit.")
    kis_supply.set_defaults(func=run_fetch_kis_supply)

    rebuild = sub.add_parser("rebuild-kis-cache", help="Rebuild KIS CSV files from saved raw JSON without API calls.")
    rebuild.add_argument("--raw-dir", default=str(DATA_DIR / "kis_raw"))
    rebuild.add_argument("--output-dir", default=str(DATA_DIR / "krx"))
    rebuild.add_argument("--date", default=None, help="YYYY-MM-DD or YYYYMMDD. Omit to reuse data/kis_market.csv date.")
    rebuild.set_defaults(func=run_rebuild_kis_cache)

    krx = sub.add_parser("fetch-krx", help="Download KRX market and foreign net-buy CSV.")
    krx.add_argument("--date", default=None, help="YYYYMMDD. Omit to use today or latest weekday.")
    krx.set_defaults(func=run_fetch_krx)

    serve = sub.add_parser("serve", help="Run local web dashboard.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8777)
    serve.set_defaults(func=run_serve)

    mobile = sub.add_parser("serve-mobile", help="Serve the mobile viewer on the local network.")
    mobile.add_argument("--host", default="0.0.0.0", help="Use 0.0.0.0 so phones on the same Wi-Fi can connect.")
    mobile.add_argument("--port", type=int, default=8788)
    mobile.add_argument("--refresh-data", dest="refresh_data", action="store_true", default=True)
    mobile.add_argument("--no-refresh-data", dest="refresh_data", action="store_false")
    mobile.add_argument("--open", action="store_true", help="Open a local Mac preview browser tab.")
    mobile.set_defaults(func=run_serve_mobile)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
