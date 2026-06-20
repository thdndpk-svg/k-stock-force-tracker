#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import DATA_DIR
from data_loader import discover_investor_csvs, discover_market_csv, load_history_dir, load_market_snapshots, merge_krx_investor_csvs
from config import get_kis_config
from global_signals import fetch_global_signals, load_global_signals
from kis_client import KisApiError, KisClient, save_json
from kis_supply import aggregate_supply_rows, load_market_csv_rows, market_rows_from_price_payload, market_rows_from_supply_rows, market_rows_from_volume_rank, merge_market_rows, normalize_live_rows, save_market_csv, save_supply_csv
from krx_downloader import fetch_krx_bundle
from scoring import ForceTracker


def chart_points(item, history) -> list[dict[str, float | str]]:
    bars = history.get(item.code, [])[-29:]
    points = [
        {
            "date": bar.trade_date.isoformat(),
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
    if not points:
        points.append({"date": "전일", "close": item.prev_close, "volume": 0.0})
    if points[-1]["date"] != item.trade_date.isoformat():
        points.append({"date": item.trade_date.isoformat(), "close": item.close, "volume": item.volume})
    return points[-30:]


def analyze_payload() -> dict[str, object]:
    market_csv = discover_market_csv(DATA_DIR)
    investor_paths = discover_investor_csvs(DATA_DIR)
    snapshots = load_market_snapshots(market_csv)
    snapshots = merge_krx_investor_csvs(snapshots, investor_paths) if investor_paths else snapshots
    history = load_history_dir(DATA_DIR / "history")
    signal_payload = load_global_signals()
    signals = signal_payload.get("signals", {})
    results = ForceTracker(snapshots, history, signals).score_all(limit=40)
    snapshots_by_code = {item.code: item for item in snapshots}
    sector_groups: dict[str, list[dict[str, object]]] = {}
    for item in results:
        sector_groups.setdefault(item.sector, []).append(
            {
                "code": item.code,
                "name": item.name,
                "score": item.discovery_score,
                "recommendation": item.recommendation,
                "theme": item.theme,
            }
        )
    return {
        "count": len(results),
        "marketCsv": str(market_csv.name),
        "investorCsv": ", ".join(path.name for path in investor_paths),
        "signals": signals,
        "signalUpdatedAt": signal_payload.get("updated_at", 0),
        "sectors": [
            {"sector": sector, "items": items[:3], "topScore": items[0]["score"] if items else 0}
            for sector, items in sorted(sector_groups.items(), key=lambda pair: pair[1][0]["score"], reverse=True)
            if sector != "기타"
        ][:8],
        "items": [
            {
                "rank": idx,
                "code": item.code,
                "name": item.name,
                "market": item.market,
                "sector": item.sector,
                "theme": item.theme,
                "grade": item.grade,
                "recommendation": item.recommendation,
                "risk": item.risk_label,
                "score": item.score,
                "discoveryScore": item.discovery_score,
                "close": item.close,
                "changeRate": item.change_rate,
                "value": item.trading_value,
                "volumeRatio": item.volume_ratio,
                "flowRatio": item.flow_ratio,
                "usImpact": item.us_impact,
                "issueScore": item.issue_score,
                "foreign": item.foreign_net_value,
                "institution": item.institution_net_value,
                "foreignAvailable": item.foreign_net_available,
                "institutionAvailable": item.institution_net_available,
                "tags": item.tags,
                "reasons": item.reasons,
                "penalties": item.penalties,
                "chart": chart_points(snapshots_by_code[item.code], history),
            }
            for idx, item in enumerate(results, 1)
        ],
    }


def with_kis_retry(call, retries: int = 4):
    for attempt in range(retries + 1):
        try:
            return call()
        except KisApiError as error:
            if "EGW00201" not in str(error) or attempt >= retries:
                raise
            time.sleep(1.2 + attempt * 0.8)


INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>국내주식 세력 매수 추적기</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4f6f8;
  --ink: #18212f;
  --muted: #697586;
  --line: #d8dee8;
  --panel: #ffffff;
  --blue: #1f6feb;
  --green: #12805c;
  --red: #d1242f;
  --amber: #9a6700;
  --purple: #6f42c1;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; background: var(--bg); color: var(--ink); }
header { background: #0f172a; color: white; padding: 22px 28px; border-bottom: 4px solid #2dd4bf; }
h1 { margin: 0; font-size: 24px; letter-spacing: 0; }
main { max-width: 1220px; margin: 0 auto; padding: 24px; }
.toolbar { display: flex; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 18px; }
button { border: 0; border-radius: 8px; background: var(--blue); color: white; padding: 10px 14px; font-weight: 700; cursor: pointer; }
button.live-on { background: #dc2626; }
.note { color: var(--muted); font-size: 13px; }
.live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; margin-right: 5px; vertical-align: 1px; }
.live-dot.on { background: #ef4444; box-shadow: 0 0 0 4px rgba(239, 68, 68, .15); }
.grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.metric { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
.metric b { display: block; font-size: 22px; margin-top: 6px; }
.insight-grid { display: grid; grid-template-columns: 1.2fr .8fr; gap: 14px; margin-bottom: 16px; }
.insight-panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
.insight-panel h2 { margin: 0 0 10px; font-size: 16px; }
.sector-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.sector-card { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #f8fafc; }
.sector-card b { display:block; font-size: 15px; margin-bottom: 6px; }
.sector-card span { display:block; color: var(--muted); font-size: 12px; line-height: 1.45; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.signal-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.signal { display:flex; justify-content:space-between; gap:8px; border:1px solid var(--line); border-radius:8px; padding:8px; background:#f8fafc; font-size:12px; }
.signal strong { font-size:13px; }
.signal .pos { color: var(--red); font-weight:800; }
.signal .neg { color: var(--blue); font-weight:800; }
.table { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 11px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { background: #edf2f7; font-size: 12px; color: #42526b; }
tr.stock-row { cursor: pointer; }
tr.stock-row:hover { background: #f8fafc; }
.score { font-weight: 800; }
.tag { display: inline-block; border-radius: 999px; padding: 3px 8px; margin: 2px 3px 2px 0; background: #e8f2ff; color: #0b5cad; font-size: 12px; }
.grade { font-weight: 800; }
.rec { display: inline-flex; min-width: 72px; justify-content: center; border-radius: 999px; padding: 5px 9px; color: white; font-size: 12px; font-weight: 800; }
.rec-strong { background: var(--red); }
.rec-buy { background: #cf5b00; }
.rec-watch { background: var(--green); }
.rec-neutral { background: #64748b; }
.rec-risk { background: #111827; }
.up { color: var(--red); font-weight: 700; }
.down { color: var(--blue); font-weight: 700; }
.flow.pos { color: var(--red); font-weight: 700; }
.flow.neg { color: var(--blue); font-weight: 700; }
.flow.none { color: var(--muted); }
.spark { width: 128px; height: 46px; display: block; cursor: pointer; }
.reasons { color: #334155; line-height: 1.45; max-width: 360px; }
.penalty { color: var(--amber); }
.modal { position: fixed; inset: 0; display: none; align-items: center; justify-content: center; padding: 24px; background: rgba(15, 23, 42, .52); z-index: 10; }
.modal.open { display: flex; }
.dialog { width: min(1120px, 96vw); max-height: 92vh; overflow: auto; background: white; border-radius: 10px; box-shadow: 0 24px 80px rgba(15,23,42,.35); }
.dialog-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; padding: 20px 22px; border-bottom: 1px solid var(--line); }
.dialog-head h2 { margin: 0 0 6px; font-size: 22px; }
.icon-btn { width: 34px; height: 34px; padding: 0; border-radius: 50%; background: #e5e7eb; color: #111827; }
.detail-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 16px 22px 4px; }
.detail-card { border: 1px solid var(--line); border-radius: 8px; padding: 11px; background: #f8fafc; }
.detail-card span { display: block; color: var(--muted); font-size: 12px; }
.detail-card b { display: block; margin-top: 5px; font-size: 17px; }
.chart-wrap { padding: 14px 22px 8px; }
.big-chart { width: 100%; height: 360px; border: 1px solid var(--line); border-radius: 8px; background: #fbfdff; }
.detail-body { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 10px 22px 22px; }
.detail-box { border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.detail-box h3 { margin: 0 0 8px; font-size: 14px; }
.detail-list { margin: 0; padding-left: 18px; line-height: 1.55; color: #334155; }
@media (max-width: 860px) {
  .grid, .detail-grid, .detail-body, .insight-grid, .sector-strip, .signal-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  table { font-size: 12px; }
  th:nth-child(7), td:nth-child(7), th:nth-child(8), td:nth-child(8) { display: none; }
}
</style>
</head>
<body>
<header><h1>국내주식 종목 발굴 랩</h1></header>
<main>
  <div class="toolbar">
    <div>
      <button id="refresh">분석 새로고침</button>
      <button id="fetchKis" style="background:#6f42c1">KIS 수급 자동수집</button>
      <button id="liveToggle">실시간 OFF</button>
      <button id="fetchGlobal" style="background:#0f766e">미국 흐름 업데이트</button>
      <button id="fetchKrx" style="background:#12805c">KRX 자동수집</button>
      <span class="note">발굴점수 = 수급집중 + 거래량이상 + 섹터/이슈 + 미국 흐름 + 차트 초입</span>
    </div>
    <span class="note" id="stamp"><span class="live-dot" id="liveDot"></span><span id="liveStatus">대기</span></span>
  </div>
  <section class="grid">
    <div class="metric">분석 종목<b id="m-count">-</b></div>
    <div class="metric">발굴 강함<b id="m-strong">-</b></div>
    <div class="metric">섹터 후보<b id="m-sector">-</b></div>
    <div class="metric">수급 집중<b id="m-flow">-</b></div>
    <div class="metric">위험 표시<b id="m-risk">-</b></div>
  </section>
  <section class="insight-grid">
    <div class="insight-panel">
      <h2>섹터별 유망 후보</h2>
      <div class="sector-strip" id="sectorCards"></div>
    </div>
    <div class="insight-panel">
      <h2>미국/글로벌 영향</h2>
      <div class="signal-list" id="signalCards"></div>
    </div>
  </section>
  <section class="table">
    <table>
      <thead>
        <tr><th>순위</th><th>종목</th><th>섹터</th><th>추천</th><th>발굴</th><th>등락</th><th>그래프</th><th>수급집중</th><th>미국</th><th>신호</th><th>근거</th></tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </section>
</main>
<section class="modal" id="detailModal">
  <div class="dialog">
    <div class="dialog-head">
      <div>
        <h2 id="d-title">종목 상세</h2>
        <div class="note" id="d-sub"></div>
      </div>
      <button class="icon-btn" id="closeDetail">×</button>
    </div>
    <div class="detail-grid">
      <div class="detail-card"><span>추천</span><b id="d-rec">-</b></div>
      <div class="detail-card"><span>발굴 점수</span><b id="d-score">-</b></div>
      <div class="detail-card"><span>위험</span><b id="d-risk">-</b></div>
      <div class="detail-card"><span>거래량</span><b id="d-vol">-</b></div>
      <div class="detail-card"><span>외국인 순매수</span><b id="d-foreign">-</b></div>
      <div class="detail-card"><span>기관 순매수</span><b id="d-inst">-</b></div>
      <div class="detail-card"><span>수급 집중</span><b id="d-flow">-</b></div>
      <div class="detail-card"><span>미국 영향</span><b id="d-us">-</b></div>
    </div>
    <div class="chart-wrap"><div class="big-chart" id="d-chart"></div></div>
    <div class="detail-body">
      <div class="detail-box"><h3>상승 근거</h3><ul class="detail-list" id="d-reasons"></ul></div>
      <div class="detail-box"><h3>위험/주의</h3><ul class="detail-list" id="d-penalties"></ul></div>
    </div>
  </div>
</section>
<script>
const fmt = new Intl.NumberFormat("ko-KR");
let currentItems = [];
let liveTimer = null;
let liveRunning = false;
let lastDataLabel = "";
const LIVE_INTERVAL_MS = 30000;
function money(v) {
  const n = Number(v || 0);
  if (Math.abs(n) >= 100000000) return `${fmt.format(Math.round(n / 100000000))}억`;
  if (Math.abs(n) >= 10000) return `${fmt.format(Math.round(n / 10000))}만`;
  return fmt.format(Math.round(n));
}
function flow(v, available) {
  if (!available) return `<span class="flow none">데이터 없음</span>`;
  const n = Number(v || 0);
  const cls = n >= 0 ? "pos" : "neg";
  return `<span class="flow ${cls}">${money(n)}</span>`;
}
function rate(v) {
  const n = Number(v || 0);
  const cls = n >= 0 ? "up" : "down";
  return `<span class="${cls}">${n.toFixed(2)}%</span>`;
}
function recClass(label) {
  if (label === "강한매수") return "rec-strong";
  if (label === "매수") return "rec-buy";
  if (label === "관심") return "rec-watch";
  if (label === "위험") return "rec-risk";
  return "rec-neutral";
}
function sparkline(points, width = 118, height = 42, big = false) {
  const values = (points || []).map(p => Number(p.close || 0)).filter(v => Number.isFinite(v));
  if (values.length < 2) return `<svg class="${big ? "big-chart" : "spark"}" viewBox="0 0 ${width} ${height}"></svg>`;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = big ? 18 : 4;
  const span = max - min || 1;
  const coords = values.map((v, i) => {
    const x = pad + i * ((width - pad * 2) / Math.max(1, values.length - 1));
    const y = height - pad - ((v - min) / span) * (height - pad * 2);
    return [x, y];
  });
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  const first = values[0];
  const color = last >= first ? "#d1242f" : "#1f6feb";
  const circles = big ? coords.map(([x,y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" fill="${color}"/>`).join("") : "";
  return `<svg class="${big ? "big-chart" : "spark"}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="${big ? 3 : 2.2}" stroke-linecap="round" stroke-linejoin="round"/>
    ${circles}
  </svg>`;
}
function detailedChart(points, width = 1040, height = 360) {
  const data = (points || []).filter(p => Number.isFinite(Number(p.close)));
  if (data.length < 2) return `<svg class="big-chart" viewBox="0 0 ${width} ${height}"></svg>`;
  const prices = data.map(p => Number(p.close));
  const volumes = data.map(p => Number(p.volume || 0));
  const ma = (arr, period) => arr.map((_, i) => {
    if (i + 1 < period) return null;
    const slice = arr.slice(i + 1 - period, i + 1);
    return slice.reduce((a, b) => a + b, 0) / period;
  });
  const ma5 = ma(prices, 5);
  const ma20 = ma(prices, 20);
  const volMa5 = ma(volumes, 5);
  const indicatorValues = prices.concat(ma5.filter(Boolean), ma20.filter(Boolean));
  const min = Math.min(...indicatorValues);
  const max = Math.max(...indicatorValues);
  const vmax = Math.max(...volumes, 1);
  const left = 64, right = 22, top = 22, bottom = 56;
  const chartH = height - top - bottom;
  const priceH = chartH * .68;
  const volTop = top + priceH + 20;
  const volH = chartH * .24;
  const span = max - min || 1;
  const xFor = i => left + i * ((width - left - right) / Math.max(1, data.length - 1));
  const yFor = v => top + priceH - ((v - min) / span) * priceH;
  const coords = prices.map((v, i) => [xFor(i), yFor(v)]);
  const line = coords.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const lineFor = arr => arr.map((v, i) => v ? `${xFor(i).toFixed(1)},${yFor(v).toFixed(1)}` : "").filter(Boolean).join(" ");
  const color = prices[prices.length - 1] >= prices[0] ? "#d1242f" : "#1f6feb";
  const bars = volumes.map((v, i) => {
    const x = xFor(i) - 4;
    const h = Math.max(1, (v / vmax) * volH);
    return `<rect x="${x.toFixed(1)}" y="${(volTop + volH - h).toFixed(1)}" width="8" height="${h.toFixed(1)}" fill="#94a3b8" opacity=".55"/>`;
  }).join("");
  const grid = [0, .25, .5, .75, 1].map(t => {
    const y = top + priceH * t;
    const price = max - span * t;
    return `<line x1="${left}" y1="${y}" x2="${width-right}" y2="${y}" stroke="#e2e8f0"/><text x="8" y="${y+4}" font-size="12" fill="#64748b">${fmt.format(Math.round(price))}</text>`;
  }).join("");
  const baseY = yFor(prices[0]);
  const volAvgLine = volMa5.map((v, i) => {
    if (!v) return "";
    const x = xFor(i);
    const y = volTop + volH - (v / vmax) * volH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean).join(" ");
  const labels = data.map((p, i) => {
    if (i !== 0 && i !== data.length - 1 && i % Math.ceil(data.length / 4) !== 0) return "";
    return `<text x="${xFor(i).toFixed(1)}" y="${height - 18}" text-anchor="middle" font-size="11" fill="#64748b">${String(p.date).slice(5)}</text>`;
  }).join("");
  return `<svg class="big-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
    <rect x="0" y="0" width="${width}" height="${height}" fill="#fbfdff"/>
    ${grid}
    <line x1="${left}" y1="${baseY}" x2="${width-right}" y2="${baseY}" stroke="#64748b" stroke-width="1.2" stroke-dasharray="5 5"/>
    ${bars}
    <polyline points="${volAvgLine}" fill="none" stroke="#0f766e" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity=".8"/>
    <line x1="${left}" y1="${volTop + volH}" x2="${width-right}" y2="${volTop + volH}" stroke="#cbd5e1"/>
    <polyline points="${line}" fill="none" stroke="${color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="${lineFor(ma5)}" fill="none" stroke="#f59e0b" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="${lineFor(ma20)}" fill="none" stroke="#7c3aed" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>
    ${coords.map(([x,y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="${color}"/>`).join("")}
    ${labels}
    <g font-size="12" font-weight="700">
      <rect x="${left}" y="6" width="360" height="22" rx="5" fill="rgba(255,255,255,.85)" stroke="#e2e8f0"/>
      <text x="${left + 10}" y="21" fill="${color}">종가</text>
      <text x="${left + 62}" y="21" fill="#f59e0b">MA5</text>
      <text x="${left + 112}" y="21" fill="#7c3aed">MA20</text>
      <text x="${left + 172}" y="21" fill="#64748b">기준선</text>
      <text x="${left + 242}" y="21" fill="#0f766e">거래량평균</text>
    </g>
    <text x="${left}" y="${volTop - 6}" font-size="12" fill="#64748b">거래량</text>
  </svg>`;
}
function esc(text) {
  return String(text || "").replace(/[&<>"']/g, s => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[s]));
}
async function load() {
  const res = await fetch("/api/analyze");
  const data = await res.json();
  currentItems = data.items || [];
  document.querySelector("#m-count").textContent = data.count;
  document.querySelector("#m-strong").textContent = data.items.filter(x => Number(x.discoveryScore) >= 75).length;
  document.querySelector("#m-sector").textContent = (data.sectors || []).length;
  document.querySelector("#m-flow").textContent = data.items.filter(x => Number(x.flowRatio) >= .05).length;
  document.querySelector("#m-risk").textContent = data.items.filter(x => x.recommendation === "위험").length;
  lastDataLabel = `${data.marketCsv || ""} ${data.investorCsv || ""}`;
  setLiveStatus(liveRunning ? "실시간 대기" : "대기");
  document.querySelector("#sectorCards").innerHTML = (data.sectors || []).map(group => `
    <div class="sector-card">
      <b>${esc(group.sector)} · ${Number(group.topScore).toFixed(1)}</b>
      ${(group.items || []).map(x => `<span>${esc(x.name)} ${Number(x.score).toFixed(1)}점 · ${esc(x.theme)}</span>`).join("")}
    </div>
  `).join("") || `<span class="note">섹터 후보 없음</span>`;
  const signalOrder = ["nasdaq", "sox", "nvidia", "tesla", "bio", "usdkrw", "oil", "china"];
  document.querySelector("#signalCards").innerHTML = signalOrder.map(key => data.signals?.[key]).filter(Boolean).map(sig => {
    const chg = Number(sig.change_pct || 0);
    return `<div class="signal"><strong>${esc(sig.label || sig.symbol)}</strong><span class="${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span></div>`;
  }).join("");
  document.querySelector("#rows").innerHTML = data.items.map(item => `
    <tr class="stock-row" onclick="openDetail('${item.code}')">
      <td>${item.rank}</td>
      <td><b>${item.name}</b><br><span class="note">${item.code} · ${item.market}</span></td>
      <td><b>${item.sector}</b><br><span class="note">${item.theme}</span></td>
      <td><span class="rec ${recClass(item.recommendation)}">${item.recommendation}</span><br><span class="note">위험 ${item.risk}</span></td>
      <td class="score">${Number(item.discoveryScore).toFixed(1)}<br><span class="note">기본 ${Number(item.score).toFixed(1)}</span></td>
      <td>${rate(item.changeRate)}<br><span class="note">${fmt.format(item.close)}원</span></td>
      <td onclick="event.stopPropagation(); openDetail('${item.code}')">${sparkline(item.chart)}</td>
      <td><b>${(Number(item.flowRatio || 0) * 100).toFixed(1)}%</b><br><span class="note">외 ${flow(item.foreign, item.foreignAvailable)} / 기 ${flow(item.institution, item.institutionAvailable)}</span></td>
      <td>${Number(item.usImpact || 0) >= 0 ? "+" : ""}${Number(item.usImpact || 0).toFixed(1)}</td>
      <td>${item.tags.map(t => `<span class="tag">${t}</span>`).join("")}</td>
      <td class="reasons">${item.reasons.join("<br>")}${item.penalties.length ? `<br><span class="penalty">${item.penalties.join("<br>")}</span>` : ""}</td>
    </tr>
  `).join("");
}
function setLiveStatus(text) {
  const dot = document.querySelector("#liveDot");
  const status = document.querySelector("#liveStatus");
  dot.classList.toggle("on", liveRunning);
  status.textContent = `${text} · ${lastDataLabel} · ${new Date().toLocaleTimeString("ko-KR")}`;
}
async function liveRefresh() {
  if (!liveRunning) return;
  const codes = currentItems.slice(0, 18).map(item => item.code).join(",");
  setLiveStatus("실시간 갱신 중");
  try {
    const res = await fetch(`/api/live-refresh?codes=${encodeURIComponent(codes)}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "실시간 갱신 실패");
    await load();
    setLiveStatus(`실시간 ON ${data.priceRows || 0}종목`);
  } catch (err) {
    setLiveStatus(`실시간 오류: ${err.message}`);
  }
}
function startLive() {
  liveRunning = true;
  document.querySelector("#liveToggle").classList.add("live-on");
  document.querySelector("#liveToggle").textContent = "실시간 ON";
  liveRefresh();
  liveTimer = setInterval(liveRefresh, LIVE_INTERVAL_MS);
}
function stopLive() {
  liveRunning = false;
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = null;
  document.querySelector("#liveToggle").classList.remove("live-on");
  document.querySelector("#liveToggle").textContent = "실시간 OFF";
  setLiveStatus("대기");
}
function openDetail(code) {
  const item = currentItems.find(x => x.code === code);
  if (!item) return;
  document.querySelector("#d-title").textContent = `${item.name} (${item.code})`;
  document.querySelector("#d-sub").textContent = `${item.market} · ${item.tags.join(" · ") || "신호 없음"}`;
  document.querySelector("#d-rec").innerHTML = `<span class="rec ${recClass(item.recommendation)}">${item.recommendation}</span>`;
  document.querySelector("#d-score").textContent = `${Number(item.discoveryScore).toFixed(1)}점`;
  document.querySelector("#d-risk").textContent = item.risk;
  document.querySelector("#d-vol").textContent = `${item.volumeRatio.toFixed(1)}배`;
  document.querySelector("#d-foreign").innerHTML = flow(item.foreign, item.foreignAvailable);
  document.querySelector("#d-inst").innerHTML = flow(item.institution, item.institutionAvailable);
  document.querySelector("#d-flow").textContent = `${(Number(item.flowRatio || 0) * 100).toFixed(1)}%`;
  document.querySelector("#d-us").textContent = `${Number(item.usImpact || 0) >= 0 ? "+" : ""}${Number(item.usImpact || 0).toFixed(1)}`;
  document.querySelector("#d-chart").innerHTML = detailedChart(item.chart);
  document.querySelector("#d-reasons").innerHTML = (item.reasons.length ? item.reasons : ["상승 근거 부족"]).map(x => `<li>${esc(x)}</li>`).join("");
  document.querySelector("#d-penalties").innerHTML = (item.penalties.length ? item.penalties : ["특별한 감점 없음"]).map(x => `<li>${esc(x)}</li>`).join("");
  document.querySelector("#detailModal").classList.add("open");
}
document.querySelector("#closeDetail").addEventListener("click", () => document.querySelector("#detailModal").classList.remove("open"));
document.querySelector("#detailModal").addEventListener("click", event => {
  if (event.target.id === "detailModal") document.querySelector("#detailModal").classList.remove("open");
});
document.querySelector("#refresh").addEventListener("click", load);
document.querySelector("#liveToggle").addEventListener("click", () => {
  if (liveRunning) stopLive();
  else startLive();
});
document.querySelector("#fetchKis").addEventListener("click", async () => {
  const btn = document.querySelector("#fetchKis");
  btn.disabled = true;
  btn.textContent = "KIS 수집 중";
  try {
    const res = await fetch("/api/fetch-kis-supply");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "KIS 수집 실패");
    btn.textContent = "KIS 완료";
    await load();
  } catch (err) {
    alert(err.message);
    btn.textContent = "KIS 수급 자동수집";
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "KIS 수급 자동수집";
    }, 1200);
  }
});
document.querySelector("#fetchGlobal").addEventListener("click", async () => {
  const btn = document.querySelector("#fetchGlobal");
  btn.disabled = true;
  btn.textContent = "미국 업데이트 중";
  try {
    const res = await fetch("/api/fetch-global");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "글로벌 지표 수집 실패");
    btn.textContent = "업데이트 완료";
    await load();
  } catch (err) {
    alert(err.message);
    btn.textContent = "미국 흐름 업데이트";
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "미국 흐름 업데이트";
    }, 1200);
  }
});
document.querySelector("#fetchKrx").addEventListener("click", async () => {
  const btn = document.querySelector("#fetchKrx");
  btn.disabled = true;
  btn.textContent = "수집 중";
  try {
    const res = await fetch("/api/fetch-krx");
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "KRX 수집 실패");
    btn.textContent = "수집 완료";
    await load();
  } catch (err) {
    alert(err.message);
    btn.textContent = "KRX 자동수집";
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "KRX 자동수집";
    }, 1200);
  }
});
load();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/api/analyze"):
            payload = json.dumps(analyze_payload(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/api/fetch-krx"):
            try:
                market_path, investor_path = fetch_krx_bundle(None, DATA_DIR)
                payload = {
                    "ok": True,
                    "market": market_path.name,
                    "investor": investor_path.name,
                }
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/fetch-kis-supply"):
            try:
                config = get_kis_config()
                if config is None:
                    raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET 설정이 필요합니다.")
                client = KisClient(config)
                foreign_rows = []
                institution_rows = []
                for market_code in ("0000", "0001", "1001"):
                    foreign_payload = with_kis_retry(lambda market_code=market_code: client.foreign_institution_total(investor="1", market_code=market_code))
                    save_json(DATA_DIR / "kis_raw" / f"foreign_institution_foreign_{market_code}.json", foreign_payload)
                    foreign_rows.extend(aggregate_supply_rows(foreign_payload, "foreign"))
                    time.sleep(1.2)
                    institution_payload = with_kis_retry(lambda market_code=market_code: client.foreign_institution_total(investor="2", market_code=market_code))
                    save_json(DATA_DIR / "kis_raw" / f"foreign_institution_institution_{market_code}.json", institution_payload)
                    institution_rows.extend(aggregate_supply_rows(institution_payload, "institution"))
                    time.sleep(1.2)
                volume_payload = with_kis_retry(client.volume_rank)
                save_json(DATA_DIR / "kis_raw" / "volume_rank.json", volume_payload)
                volume_rows = market_rows_from_volume_rank(volume_payload)
                foreign_path = save_supply_csv(DATA_DIR / "krx" / "kis_외국인_순매수.csv", foreign_rows)
                institution_path = save_supply_csv(DATA_DIR / "krx" / "kis_기관_순매수.csv", institution_rows)
                market_path = save_market_csv(DATA_DIR / "kis_market.csv", merge_market_rows(market_rows_from_supply_rows(foreign_rows + institution_rows) + volume_rows))
                payload = {
                    "ok": True,
                    "foreign": foreign_path.name,
                    "institution": institution_path.name,
                    "market": market_path.name,
                    "foreignRows": len(foreign_rows),
                    "institutionRows": len(institution_rows),
                    "volumeRows": len(volume_rows),
                }
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/live-refresh"):
            try:
                config = get_kis_config()
                if config is None:
                    raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET 설정이 필요합니다.")
                client = KisClient(config)
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                codes = [
                    code.strip().zfill(6)
                    for code in ",".join(query.get("codes", [])).split(",")
                    if code.strip()
                ][:18]
                existing_rows = load_market_csv_rows(DATA_DIR / "kis_market.csv")
                existing_by_code = {str(row.get("code", "")).zfill(6): row for row in existing_rows}

                volume_payload = with_kis_retry(client.volume_rank)
                save_json(DATA_DIR / "kis_raw" / "live_volume_rank.json", volume_payload)
                volume_rows = market_rows_from_volume_rank(volume_payload)

                price_rows = []
                for code in codes:
                    fallback = existing_by_code.get(code, {})
                    price_payload = with_kis_retry(lambda code=code: client.inquire_price(code))
                    price_rows.extend(
                        market_rows_from_price_payload(
                            price_payload,
                            fallback_code=code,
                            fallback_name=str(fallback.get("name") or code),
                        )
                    )
                    time.sleep(0.25)
                price_rows = normalize_live_rows(price_rows, existing_rows)
                market_path = save_market_csv(
                    DATA_DIR / "kis_market.csv",
                    merge_market_rows(existing_rows + volume_rows + price_rows, prefer_latest=True),
                )
                payload = {
                    "ok": True,
                    "market": market_path.name,
                    "volumeRows": len(volume_rows),
                    "priceRows": len(price_rows),
                    "updatedAt": int(time.time()),
                }
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/fetch-global"):
            try:
                payload = {"ok": True, "data": fetch_global_signals()}
            except Exception as error:
                payload = {"ok": False, "error": str(error)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if payload["ok"] else 500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = INDEX_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def run_server(host: str = "127.0.0.1", port: int = 8777) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"K-Stock Force Tracker: {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    run_server()
