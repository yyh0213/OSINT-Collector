"""
reliability_viewer.py
---------------------
Serves reliability.db data as a JSON API + static HTML dashboard.
Run: python reliability_viewer.py
Open: http://localhost:5050
"""

import sqlite3
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

DB_PATH = os.path.join(os.path.dirname(__file__), "config", "reliability.db")
PORT = 5050


def query_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM source_reliability ORDER BY avg_richness_score DESC")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Source Reliability Dashboard</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    :root {
      --bg: #0d0f18;
      --surface: #151823;
      --surface2: #1c2032;
      --border: rgba(255,255,255,0.07);
      --accent: #6c63ff;
      --accent2: #00d4aa;
      --danger: #ff4d6d;
      --warn: #ffb703;
      --text: #e8eaf0;
      --muted: #707585;
      --trusted: #00d4aa;
      --warning: #ffb703;
      --untrusted: #ff4d6d;
      --unknown: #707585;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }

    /* ── Header ── */
    header {
      background: linear-gradient(135deg, #1a1d2e 0%, #0d1117 100%);
      border-bottom: 1px solid var(--border);
      padding: 24px 40px;
      display: flex;
      align-items: center;
      gap: 16px;
    }
    header .icon {
      width: 44px; height: 44px;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      border-radius: 12px;
      display: flex; align-items: center; justify-content: center;
      font-size: 20px;
    }
    header h1 {
      font-size: 1.4rem; font-weight: 700;
      background: linear-gradient(90deg, #fff, #a0aacc);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    header p { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
    .header-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
    #last-updated { font-size: 0.75rem; color: var(--muted); }
    #refresh-btn {
      background: var(--surface2);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 16px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 0.8rem;
      font-family: inherit;
      transition: all 0.2s;
    }
    #refresh-btn:hover { background: var(--accent); border-color: var(--accent); }

    /* ── Stats bar ── */
    .stats-bar {
      display: flex; gap: 16px;
      padding: 24px 40px;
      flex-wrap: wrap;
    }
    .stat-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px 24px;
      flex: 1; min-width: 140px;
      position: relative; overflow: hidden;
      transition: transform 0.2s;
    }
    .stat-card:hover { transform: translateY(-2px); }
    .stat-card::before {
      content: '';
      position: absolute; top: 0; left: 0; right: 0; height: 3px;
    }
    .stat-card.all::before { background: linear-gradient(90deg, var(--accent), var(--accent2)); }
    .stat-card.trusted::before { background: var(--trusted); }
    .stat-card.warning::before { background: var(--warn); }
    .stat-card.untrusted::before { background: var(--untrusted); }
    .stat-label { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
    .stat-value { font-size: 2rem; font-weight: 700; margin-top: 6px; }
    .stat-card.trusted .stat-value { color: var(--trusted); }
    .stat-card.warning .stat-value { color: var(--warn); }
    .stat-card.untrusted .stat-value { color: var(--untrusted); }

    /* ── Controls ── */
    .controls {
      padding: 0 40px 20px;
      display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    }
    .search-wrap {
      position: relative; flex: 1; min-width: 200px; max-width: 360px;
    }
    .search-wrap input {
      width: 100%;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 10px 16px 10px 40px;
      border-radius: 10px;
      font-family: inherit; font-size: 0.85rem;
      outline: none; transition: border-color 0.2s;
    }
    .search-wrap input:focus { border-color: var(--accent); }
    .search-icon {
      position: absolute; left: 13px; top: 50%; transform: translateY(-50%);
      color: var(--muted); pointer-events: none; font-size: 14px;
    }
    .filter-group { display: flex; gap: 6px; }
    .filter-btn {
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--muted);
      padding: 9px 16px;
      border-radius: 8px;
      cursor: pointer; font-size: 0.8rem;
      font-family: inherit; transition: all 0.2s;
    }
    .filter-btn.active { color: var(--text); border-color: var(--accent); background: rgba(108,99,255,0.15); }
    .filter-btn:hover:not(.active) { color: var(--text); border-color: var(--border); background: var(--surface2); }

    /* ── Table ── */
    .table-wrap {
      padding: 0 40px 40px;
      overflow-x: auto;
    }
    table {
      width: 100%; border-collapse: collapse;
      background: var(--surface);
      border-radius: 16px; overflow: hidden;
      border: 1px solid var(--border);
    }
    thead { background: var(--surface2); }
    th {
      padding: 14px 18px;
      text-align: left;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      transition: color 0.2s;
    }
    th:hover { color: var(--text); }
    th .sort-icon { margin-left: 4px; opacity: 0.4; }
    th.sorted .sort-icon { opacity: 1; color: var(--accent); }
    tbody tr {
      border-top: 1px solid var(--border);
      transition: background 0.15s;
    }
    tbody tr:hover { background: var(--surface2); }
    td {
      padding: 14px 18px;
      font-size: 0.85rem;
      vertical-align: middle;
    }
    .source-name { font-weight: 500; }
    .source-id { font-size: 0.72rem; color: var(--muted); margin-top: 2px; font-family: monospace; }

    /* score bar */
    .score-cell { display: flex; align-items: center; gap: 10px; }
    .score-bar-bg {
      flex: 1; height: 6px; border-radius: 3px;
      background: rgba(255,255,255,0.07); max-width: 80px;
    }
    .score-bar-fill {
      height: 100%; border-radius: 3px;
      background: linear-gradient(90deg, var(--accent), var(--accent2));
      transition: width 0.6s ease;
    }
    .score-num { font-weight: 600; min-width: 36px; text-align: right; font-size: 0.82rem; }

    /* status badge */
    .badge {
      display: inline-flex; align-items: center; gap: 5px;
      padding: 4px 10px; border-radius: 20px;
      font-size: 0.72rem; font-weight: 600; letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .badge::before { content: ''; width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
    .badge.TRUSTED { background: rgba(0,212,170,0.12); color: var(--trusted); }
    .badge.TRUSTED::before { background: var(--trusted); box-shadow: 0 0 6px var(--trusted); }
    .badge.WARNING { background: rgba(255,183,3,0.12); color: var(--warn); }
    .badge.WARNING::before { background: var(--warn); box-shadow: 0 0 6px var(--warn); }
    .badge.UNTRUSTED { background: rgba(255,77,109,0.12); color: var(--untrusted); }
    .badge.UNTRUSTED::before { background: var(--untrusted); box-shadow: 0 0 6px var(--untrusted); }
    .badge.UNKNOWN { background: rgba(112,117,133,0.12); color: var(--unknown); }
    .badge.UNKNOWN::before { background: var(--unknown); }

    .num-cell { font-variant-numeric: tabular-nums; }
    .strike { color: var(--danger); font-weight: 600; }

    /* empty state */
    .empty { text-align: center; padding: 60px; color: var(--muted); }
    .empty .e-icon { font-size: 3rem; margin-bottom: 12px; }

    /* loading */
    #loading {
      position: fixed; inset: 0;
      background: var(--bg);
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      z-index: 999; transition: opacity 0.4s;
    }
    .spinner {
      width: 44px; height: 44px;
      border: 3px solid var(--border);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    #loading p { margin-top: 16px; color: var(--muted); font-size: 0.85rem; }

    /* error banner */
    #error-banner {
      display: none; margin: 0 40px 20px;
      background: rgba(255,77,109,0.1);
      border: 1px solid rgba(255,77,109,0.3);
      color: var(--danger);
      padding: 14px 20px; border-radius: 10px;
      font-size: 0.85rem;
    }

    @media (max-width: 600px) {
      header, .stats-bar, .controls, .table-wrap { padding-left: 16px; padding-right: 16px; }
    }
  </style>
</head>
<body>

<div id="loading">
  <div class="spinner"></div>
  <p>데이터 로딩 중...</p>
</div>

<header>
  <div class="icon">🛡️</div>
  <div>
    <h1>Source Reliability Dashboard</h1>
    <p>intelligence / config / reliability.db</p>
  </div>
  <div class="header-right">
    <span id="last-updated"></span>
    <button id="refresh-btn" onclick="loadData()">↻ 새로고침</button>
  </div>
</header>

<div class="stats-bar">
  <div class="stat-card all">
    <div class="stat-label">전체 소스</div>
    <div class="stat-value" id="cnt-all">—</div>
  </div>
  <div class="stat-card trusted">
    <div class="stat-label">Trusted</div>
    <div class="stat-value" id="cnt-trusted">—</div>
  </div>
  <div class="stat-card warning">
    <div class="stat-label">Warning</div>
    <div class="stat-value" id="cnt-warning">—</div>
  </div>
  <div class="stat-card untrusted">
    <div class="stat-label">Untrusted</div>
    <div class="stat-value" id="cnt-untrusted">—</div>
  </div>
</div>

<div class="controls">
  <div class="search-wrap">
    <span class="search-icon">🔍</span>
    <input id="search-input" type="text" placeholder="소스명 검색..." oninput="applyFilters()"/>
  </div>
  <div class="filter-group">
    <button class="filter-btn active" id="filter-ALL" onclick="setFilter('ALL')">전체</button>
    <button class="filter-btn" id="filter-TRUSTED" onclick="setFilter('TRUSTED')">Trusted</button>
    <button class="filter-btn" id="filter-WARNING" onclick="setFilter('WARNING')">Warning</button>
    <button class="filter-btn" id="filter-UNTRUSTED" onclick="setFilter('UNTRUSTED')">Untrusted</button>
  </div>
</div>

<div id="error-banner">⚠️ <span id="error-msg"></span></div>

<div class="table-wrap">
  <table id="main-table">
    <thead>
      <tr>
        <th onclick="sortBy('source_name')">소스명 <span class="sort-icon">↕</span></th>
        <th onclick="sortBy('total_articles')" class="num-cell">기사 수 <span class="sort-icon">↕</span></th>
        <th onclick="sortBy('copycat_strikes')" class="num-cell">표절 횟수 <span class="sort-icon">↕</span></th>
        <th onclick="sortBy('avg_lag_time_mins')" class="num-cell">평균 지연(분) <span class="sort-icon">↕</span></th>
        <th onclick="sortBy('avg_richness_score')">풍부도 점수 <span class="sort-icon">↕</span></th>
        <th onclick="sortBy('delta_contribution')" class="num-cell">델타 기여도 <span class="sort-icon">↕</span></th>
        <th onclick="sortBy('status')">상태 <span class="sort-icon">↕</span></th>
        <th onclick="sortBy('last_evaluated')">마지막 평가 <span class="sort-icon">↕</span></th>
      </tr>
    </thead>
    <tbody id="table-body">
    </tbody>
  </table>
</div>

<script>
  let allData = [];
  let currentFilter = 'ALL';
  let sortKey = 'avg_richness_score';
  let sortAsc = false;

  async function loadData() {
    try {
      const res = await fetch('/api/reliability');
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      allData = data;
      updateStats();
      applyFilters();
      document.getElementById('last-updated').textContent =
        '업데이트: ' + new Date().toLocaleTimeString('ko-KR');
      document.getElementById('error-banner').style.display = 'none';
    } catch(e) {
      document.getElementById('error-banner').style.display = 'block';
      document.getElementById('error-msg').textContent = '데이터 로드 실패: ' + e.message;
    } finally {
      const loading = document.getElementById('loading');
      loading.style.opacity = '0';
      setTimeout(() => loading.style.display = 'none', 400);
    }
  }

  function updateStats() {
    document.getElementById('cnt-all').textContent = allData.length;
    document.getElementById('cnt-trusted').textContent = allData.filter(r => r.status === 'TRUSTED').length;
    document.getElementById('cnt-warning').textContent = allData.filter(r => r.status === 'WARNING').length;
    document.getElementById('cnt-untrusted').textContent = allData.filter(r => r.status === 'UNTRUSTED').length;
  }

  function setFilter(f) {
    currentFilter = f;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('filter-' + f).classList.add('active');
    applyFilters();
  }

  function applyFilters() {
    const q = document.getElementById('search-input').value.toLowerCase();
    let rows = allData;
    if (currentFilter !== 'ALL') rows = rows.filter(r => r.status === currentFilter);
    if (q) rows = rows.filter(r =>
      r.source_name.toLowerCase().includes(q) ||
      r.source_id.toLowerCase().includes(q)
    );
    // sort
    rows = rows.slice().sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      if (typeof av === 'string') av = av.toLowerCase(), bv = bv.toLowerCase();
      return sortAsc ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
    renderTable(rows);
  }

  function sortBy(key) {
    if (sortKey === key) sortAsc = !sortAsc;
    else { sortKey = key; sortAsc = false; }
    document.querySelectorAll('th').forEach(th => th.classList.remove('sorted'));
    event.currentTarget.classList.add('sorted');
    applyFilters();
  }

  function renderTable(rows) {
    const tbody = document.getElementById('table-body');
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8"><div class="empty"><div class="e-icon">🔍</div>검색 결과 없음</div></td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => {
      const score = r.avg_richness_score;
      const pct = Math.min(100, Math.max(0, (score / 10) * 100));
      const scoreColor = score >= 7 ? 'var(--trusted)' : score >= 4 ? 'var(--warn)' : 'var(--danger)';
      const dt = r.last_evaluated ? r.last_evaluated.replace('T', ' ').slice(0, 16) : '—';
      const lag = r.avg_lag_time_mins != null ? (+r.avg_lag_time_mins).toFixed(1) : '—';
      const delta = r.delta_contribution != null ? (+r.delta_contribution).toFixed(2) : '—';
      const strikeHtml = r.copycat_strikes > 0
        ? `<span class="strike">⚠ ${r.copycat_strikes}</span>`
        : `<span style="color:var(--muted)">0</span>`;
      return `<tr>
        <td>
          <div class="source-name">${r.source_name}</div>
          <div class="source-id">${r.source_id}</div>
        </td>
        <td class="num-cell">${r.total_articles}</td>
        <td class="num-cell">${strikeHtml}</td>
        <td class="num-cell">${lag}</td>
        <td>
          <div class="score-cell">
            <div class="score-bar-bg">
              <div class="score-bar-fill" style="width:${pct}%; background: linear-gradient(90deg, var(--accent), ${scoreColor})"></div>
            </div>
            <span class="score-num" style="color:${scoreColor}">${score.toFixed(2)}</span>
          </div>
        </td>
        <td class="num-cell">${delta}</td>
        <td><span class="badge ${r.status}">${r.status}</span></td>
        <td style="color:var(--muted); font-size:0.78rem;">${dt}</td>
      </tr>`;
    }).join('');
  }

  loadData();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/reliability":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                data = query_db()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                err = json.dumps({"error": str(e)}, ensure_ascii=False)
                self.wfile.write(err.encode("utf-8"))
        elif parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[OK] Reliability Dashboard 실행 중")
    print(f"   -> http://localhost:{PORT}")
    print(f"   -> DB: {DB_PATH}")
    print("   Ctrl+C 로 종료")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료.")
