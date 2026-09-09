"use strict";

const GRID = "rgba(16,32,58,.07)";
const TICK = "#7a8aa0";
const fmt = (n) => (n ?? 0).toLocaleString("ko-KR");
const $ = (id) => document.getElementById(id);
const pad2 = (n) => String(n).padStart(2, "0");

let state = { gran: "day", start: null, end: null, channels: [] };
let master = [];
let colorMap = {};
let ctMode = "line";
let lastView = null;
let DATA = null;           // { inflow, channels, log } — 정적 JSON 캐시
const charts = {};

/* ============================== data range helpers */
const QUICK = {
  day: [
    ["오늘", (mx) => [mx, mx]],
    ["어제", (mx) => { const d = new Date(mx); d.setDate(d.getDate() - 1); const s = iso(d); return [s, s]; }],
    ["최근 7일", (mx) => [addDays(mx, -6), mx]],
    ["최근 30일", (mx) => [addDays(mx, -29), mx]],
    ["이번 달", (mx) => [mx.slice(0, 7) + "-01", mx]],
  ],
  month: [
    ["최근 3개월", (mx) => [addMonths(mx, -2), mx]],
    ["최근 6개월", (mx) => [addMonths(mx, -5), mx]],
    ["최근 12개월", (mx) => [addMonths(mx, -11), mx]],
    ["올해", (mx) => [mx.slice(0, 4) + "-01", mx]],
  ],
  year: [
    ["최근 2년", (mx) => [String(+mx - 1), mx]],
    ["최근 3년", (mx) => [String(+mx - 2), mx]],
  ],
};
const iso = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
function addDays(s, n) { const d = new Date(s); d.setDate(d.getDate() + n); return iso(d); }
function addMonths(s, n) { const [y, m] = s.split("-").map(Number); const d = new Date(y, m - 1 + n, 1); return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`; }

function maxBucket(v, gran) {
  const dmax = (v && v.as_of.date_max) || iso(new Date());
  return gran === "year" ? dmax.slice(0, 4) : gran === "month" ? dmax.slice(0, 7) : dmax;
}
function minBucket(v, gran) {
  const dmin = (v && v.as_of.date_min) || iso(new Date());
  return gran === "year" ? dmin.slice(0, 4) : gran === "month" ? dmin.slice(0, 7) : dmin;
}

/* ============================== data load (암호화 번들) */
async function fetchData(force) {
  if (DATA && !force) return DATA;
  const b = await window.AUTH.loadBundle(force);   // {inflow, channels, log}
  DATA = { inflow: b.inflow || { records: [] }, channels: b.channels || { channels: [] }, log: b.log || { logs: [] } };
  const codes = (DATA.channels.channels || []).map((c) => c.code);
  const sample = codes.includes("PARTNER_A") || /샘플|가상|sample/i.test(DATA.inflow._comment || "");
  const fs = $("foot-sample"); if (fs) fs.hidden = !sample;
  return DATA;
}

/* ============================== load / render */
async function load(force) {
  if (!window.AUTH.requireLogin()) return;
  $("loading").hidden = false;
  try {
    let v;
    try {
      const d = await fetchData(force);
      v = window.ANALYTICS.buildView(
        { granularity: state.gran, start: state.start, end: state.end, channels: state.channels },
        d,
      );
    } catch (e) {
      banner("err", "데이터를 불러오지 못했습니다: " + e.message, true);
      return;
    }
    lastView = v;
    render(v);
  } catch (e) {
    console.error(e);
    banner("err", "화면 표시 중 오류: " + e.message, true);
  } finally {
    $("loading").hidden = true;
  }
}

function render(v) {
  const L = v.labels;
  $("m-latest").textContent = v.as_of.latest_date || "–";
  $("m-collected").textContent = v.as_of.last_collected_at
    ? new Date(v.as_of.last_collected_at).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" }) : "–";
  $("m-count").textContent = v.as_of.record_count;

  syncRangeInputs(v);
  buildChannelChips(v);
  buildQuickChips();
  $("h-trend").textContent = `${L.unit}별 유입 추이`;
  $("h-chtrend").textContent = `제휴채널별 유입 추이 (${L.unit}별)`;
  $("table-range").textContent = `${v.query.start} ~ ${v.query.end} · ${L.unit}별`;
  updateHint(v);

  renderKPI(v);
  drawTrend(v);
  drawChannelTrend(v);
  drawBarShare(v);
  drawTable(v);
}

/* ============================== KPI */
function renderKPI(v) {
  const k = v.kpi, L = v.labels;
  $("k-today-label").textContent = L.latest;
  $("k-dod-label").textContent = L.delta;
  $("k-today-badge").textContent = (k.today.date || "") + (k.latest_partial ? " · 진행 중" : "");
  $("k-today").textContent = fmt(k.today.total);
  $("k-today-sub").textContent = `마케팅동의 ${fmt(k.today.y)} · 미동의 ${fmt(k.today.n)}`;
  $("k-today-spark").innerHTML = spark(v.daily_trend.map((d) => d.total), "#0067ac", true);

  $("k-period").textContent = fmt(k.period_total.total);
  const rate = k.period_total.total ? (k.period_total.y / k.period_total.total * 100).toFixed(1) : 0;
  $("k-period-sub").textContent = `동의 ${fmt(k.period_total.y)} · 미동의 ${fmt(k.period_total.n)} · 동의율 ${rate}%`;
  $("k-period-spark").innerHTML = spark(v.cumulative_trend.map((d) => d.total), "#00a1b5", true);

  const d = $("k-dod");
  if (k.dod.total_pct === null || k.dod.prev_date === null) {
    d.textContent = "–"; d.className = "k-value delta flat";
    $("k-dod-chip").innerHTML = "";
    $("k-dod-sub").textContent = k.dod.note || "비교 데이터 없음";
  } else {
    const up = k.dod.total_pct > 0, flat = k.dod.total_pct === 0;
    d.textContent = `${up ? "▲" : flat ? "―" : "▼"} ${Math.abs(k.dod.total_pct)}%`;
    d.className = "k-value delta " + (flat ? "flat" : up ? "up" : "down");
    $("k-dod-chip").innerHTML = `<span class="chip-delta ${flat ? "flat" : up ? "up" : "down"}">${k.dod.total_diff > 0 ? "+" : ""}${fmt(k.dod.total_diff)}건</span>`;
    $("k-dod-sub").textContent = `${k.dod.prev_date} 대비`;
  }
  $("k-dod-spark").innerHTML = spark(v.daily_trend.slice(-14).map((x) => x.total), "#8a63c9", false, true);

  $("k-active").textContent = k.active_channels;
  $("k-active-sub").textContent = k.channels_in_view === k.active_channels
    ? "Dashboard 노출 채널" : `조회 조건에 ${k.channels_in_view}개 포함`;
}

/* ============================== filters UI */
function setGran(g) {
  state.gran = g;
  document.querySelectorAll("#gran button").forEach((b) => b.classList.toggle("on", b.dataset.g === g));
  document.querySelectorAll(".range-block").forEach((el) => (el.hidden = el.dataset.mode !== g));
  state.start = state.end = null;
  buildQuickChips();
  load();
}

function syncRangeInputs(v) {
  const g = v.query.granularity;
  if (g === "day") { $("d-start").value = v.query.start; $("d-end").value = v.query.end; }
  else if (g === "month") { $("m-start").value = v.query.start; $("m-end").value = v.query.end; }
  else {
    const sy = $("y-start"), ey = $("y-end");
    if (!sy.dataset.built) {
      const lo = +minBucket(v, "year"), hi = +maxBucket(v, "year");
      const opts = [];
      for (let y = lo; y <= hi + 1; y++) opts.push(`<option value="${y}">${y}년</option>`);
      sy.innerHTML = ey.innerHTML = opts.join("");
      sy.dataset.built = ey.dataset.built = "1";
    }
    sy.value = v.query.start; ey.value = v.query.end;
  }
  state.start = v.query.start; state.end = v.query.end;
}

function readRange() {
  const g = state.gran;
  if (g === "day") { state.start = $("d-start").value || null; state.end = $("d-end").value || null; }
  else if (g === "month") { state.start = $("m-start").value || null; state.end = $("m-end").value || null; }
  else { state.start = $("y-start").value || null; state.end = $("y-end").value || null; }
}

function buildQuickChips() {
  const box = $("quick");
  box.innerHTML = (QUICK[state.gran] || []).map((q, i) => `<button class="chip quick" data-i="${i}">${q[0]}</button>`).join("");
  box.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    const mx = maxBucket(lastView, state.gran);
    const [s, e] = QUICK[state.gran][+b.dataset.i][1](mx);
    state.start = s; state.end = e;
    load();
  }));
}

function buildChannelChips(v) {
  master = v.channel_master;
  colorMap = {};
  master.forEach((c) => (colorMap[c.code] = c.color));
  const cc = $("f-channels");
  if (cc.dataset.built) return;
  cc.innerHTML = master.map((c) =>
    `<button class="chip" data-code="${c.code}">
      <span class="ch-dot" style="background:${c.color}"></span>${c.name}</button>`).join("");
  cc.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    c.classList.toggle("on");
    state.channels = [...cc.querySelectorAll(".chip.on")].map((x) => x.dataset.code);
  }));
  cc.dataset.built = "1";
}

function updateHint(v) {
  const ch = state.channels.length ? `선택 채널 ${state.channels.length}개` : "전체 채널";
  $("filter-hint").textContent = `${ch} · ${v.query.start} ~ ${v.query.end} (${v.labels.unit}별)`;
}

/* ============================== charts */
function baseOpts(extra = {}) {
  return Object.assign({
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#0a2647", padding: 10, cornerRadius: 8,
        callbacks: { label: (c) => ` ${c.dataset.label || c.label}: ${fmt(c.parsed.y ?? c.parsed)}건` },
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: TICK, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 14 } },
      y: { beginAtZero: true, border: { display: false }, grid: { color: GRID }, ticks: { color: TICK, font: { size: 11 }, callback: fmt } },
    },
  }, extra);
}
function grad(ctx, hex) {
  const g = ctx.createLinearGradient(0, 0, 0, 240);
  g.addColorStop(0, hex + "33"); g.addColorStop(1, hex + "03"); return g;
}
function mk(id, type, dataFn, options) {
  const ctx = $(id).getContext("2d");
  const data = dataFn(ctx);
  if (charts[id]) { charts[id].destroy(); }
  charts[id] = new Chart(ctx, { type, data, options });
}

function drawTrend(v) {
  const labels = v.daily_trend.map((d) => d.date);
  mk("c-daily", "line", (ctx) => ({
    labels,
    datasets: [{
      label: "유입", data: v.daily_trend.map((d) => d.total),
      borderColor: "#0067ac", backgroundColor: grad(ctx, "#0067ac"), fill: true,
      borderWidth: 2, tension: .3, pointRadius: labels.length > 40 ? 0 : 3,
      pointBackgroundColor: "#0067ac", pointBorderColor: "#fff", pointBorderWidth: 1.5,
    }],
  }), baseOpts());

  mk("c-cumul", "line", (ctx) => ({
    labels: v.cumulative_trend.map((d) => d.date),
    datasets: [{
      label: "누적", data: v.cumulative_trend.map((d) => d.total),
      borderColor: "#00a1b5", backgroundColor: grad(ctx, "#00a1b5"), fill: true,
      borderWidth: 2, tension: .15, pointRadius: 0,
    }],
  }), baseOpts());
}

function drawChannelTrend(v) {
  const labels = v.buckets;
  const isBar = ctMode === "bar";
  const datasets = v.by_channel.map((c) => ({
    label: c.name,
    data: c.series.map((s) => s.total),
    borderColor: c.color,
    backgroundColor: isBar ? c.color : (c.color + "22"),
    borderWidth: 2, fill: false, tension: .3,
    pointRadius: labels.length > 30 ? 0 : 2.5,
    stack: isBar ? "all" : undefined,
  }));
  mk("c-chtrend", isBar ? "bar" : "line", () => ({ labels, datasets }), baseOpts({
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: "#0a2647", padding: 10, cornerRadius: 8,
        itemSort: (a, b) => b.parsed.y - a.parsed.y,
        callbacks: { label: (c) => ` ${c.dataset.label}: ${fmt(c.parsed.y)}건` },
      },
    },
    scales: {
      x: { grid: { display: false }, stacked: isBar, ticks: { color: TICK, font: { size: 11 }, maxRotation: 0, autoSkipPadding: 14 } },
      y: { beginAtZero: true, stacked: isBar, border: { display: false }, grid: { color: GRID }, ticks: { color: TICK, font: { size: 11 }, callback: fmt } },
    },
  }));

  $("ct-legend").innerHTML = v.by_channel.map((c) =>
    `<span class="ct-li"><span class="ct-sw" style="background:${c.color}"></span>${c.name} <b>${fmt(c.period_total)}</b></span>`).join("");
}

function drawBarShare(v) {
  const bar = v.by_channel;
  mk("c-bar", "bar", () => ({
    labels: bar.map((c) => c.name),
    datasets: [{ label: "기간 누적", data: bar.map((c) => c.period_total),
      backgroundColor: bar.map((c) => c.color), borderRadius: 6, maxBarThickness: 46 }],
  }), baseOpts());

  const share = v.channel_share;
  mk("c-share", "doughnut", () => ({
    labels: share.map((s) => s.name),
    datasets: [{ data: share.map((s) => s.total), backgroundColor: share.map((s) => s.color),
      borderWidth: 2, borderColor: "#fff", hoverOffset: 6 }],
  }), {
    responsive: true, maintainAspectRatio: false, cutout: "62%",
    plugins: {
      legend: { display: true, position: "right", labels: { boxWidth: 10, boxHeight: 10, padding: 10, font: { size: 11 }, color: "#45566e" } },
      tooltip: { backgroundColor: "#0a2647", padding: 10, cornerRadius: 8,
        callbacks: { label: (c) => ` ${c.label}: ${fmt(c.parsed)}건 (${share[c.dataIndex].pct}%)` } },
    },
  });
}

/* ============================== sparkline / table */
function spark(vals, color, fill, showLast) {
  if (!vals.length) return "";
  const w = 200, h = 30, pad = 2;
  const mx = Math.max(...vals, 1), mn = Math.min(...vals, 0), sp = mx - mn || 1;
  const xs = (i) => vals.length === 1 ? w / 2 : (i / (vals.length - 1)) * (w - pad * 2) + pad;
  const ys = (val) => h - pad - ((val - mn) / sp) * (h - pad * 2);
  const pts = vals.map((val, i) => `${xs(i).toFixed(1)},${ys(val).toFixed(1)}`).join(" ");
  const area = fill ? `<polygon points="${xs(0).toFixed(1)},${h} ${pts} ${xs(vals.length - 1).toFixed(1)},${h}" fill="${color}" opacity=".12"/>` : "";
  const dot = showLast ? `<circle cx="${xs(vals.length - 1).toFixed(1)}" cy="${ys(vals[vals.length - 1]).toFixed(1)}" r="2.6" fill="${color}"/>` : "";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${area}<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>${dot}</svg>`;
}
function deltaCell(pct) {
  if (pct === null || pct === undefined) return `<span class="delta flat">–</span>`;
  const up = pct > 0, flat = pct === 0;
  return `<span class="delta ${flat ? "flat" : up ? "up" : "down"}">${up ? "▲" : flat ? "―" : "▼"} ${Math.abs(pct)}%</span>`;
}
function drawTable(v) {
  $("th-latest").textContent = v.labels.col;
  $("th-dod").textContent = v.labels.delta;
  const body = $("detail-body"); body.innerHTML = "";
  v.by_channel.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="ch-name"><span class="ch-dot" style="background:${c.color}"></span>
        ${c.name}<span class="badge-type">${c.type || "-"}</span></span></td>
      <td>${fmt(c.today_total)}</td>
      <td>${fmt(c.today_y)} / ${fmt(c.today_n)}</td>
      <td>${fmt(c.period_total)}</td>
      <td>${c.share_pct}%</td>
      <td>${deltaCell(c.dod_pct)}</td>
      <td>${spark(c.series.map((s) => s.total), c.color, false)}</td>`;
    body.appendChild(tr);
  });
  const k = v.kpi;
  $("detail-foot").innerHTML = `<tr>
    <td>합계</td><td>${fmt(k.today.total)}</td><td>${fmt(k.today.y)} / ${fmt(k.today.n)}</td>
    <td>${fmt(k.period_total.total)}</td><td>100%</td><td>${deltaCell(k.dod.total_pct)}</td><td></td></tr>`;
}

/* ============================== banner + events */
let bt;
function banner(kind, msg, sticky) {
  const b = $("banner"); b.className = "banner show " + kind; b.textContent = msg;
  clearTimeout(bt); if (!sticky) bt = setTimeout(() => (b.className = "banner"), 6000);
}

document.querySelectorAll("#gran button").forEach((b) => b.addEventListener("click", () => setGran(b.dataset.g)));
$("btn-apply").addEventListener("click", () => { readRange(); load(); });
$("btn-reset").addEventListener("click", () => {
  state = { gran: state.gran, start: null, end: null, channels: [] };
  document.querySelectorAll("#f-channels .chip").forEach((c) => c.classList.remove("on"));
  load();
});
document.querySelectorAll(".mini-toggle").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".mini-toggle").forEach((x) => x.classList.toggle("on", x === b));
  ctMode = b.dataset.ctmode;
  if (lastView) drawChannelTrend(lastView);
}));

$("btn-pdf").addEventListener("click", () => {
  const p = new URLSearchParams({ granularity: state.gran });
  if (state.start) p.set("start", state.start);
  if (state.end) p.set("end", state.end);
  if (state.channels.length) p.set("channels", state.channels.join(","));
  window.open("report.html?" + p.toString(), "jehyu_report");
});

$("btn-refresh").addEventListener("click", async () => {
  const btn = $("btn-refresh");
  btn.disabled = true;
  try { await load(true); banner("ok", "데이터를 다시 불러왔습니다."); }
  catch (e) { banner("err", "새로고침 실패: " + e.message, true); }
  finally { btn.disabled = false; }
});

$("btn-logout").addEventListener("click", () => window.AUTH.logout());
(function () {
  const u = window.AUTH.currentUser();
  if (u) $("who").textContent = `${u.name}${u.role === "admin" ? " · 관리자" : ""}`;
})();

load();
