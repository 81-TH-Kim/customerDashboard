"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (m) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
let channels = [];
let channelsMeta = {};
let BUNDLE = null;
let usersDoc = null;

if (!window.AUTH.requireLogin()) { throw new Error("redirect to login"); }

let bt;
function banner(kind, msg, sticky) {
  const b = $("banner");
  b.className = "banner show " + kind; b.textContent = msg;
  clearTimeout(bt); if (!sticky) bt = setTimeout(() => (b.className = "banner"), 6000);
}

const me = window.AUTH.currentUser();
if (me) $("who").textContent = `${me.name}${me.role === "admin" ? " · 관리자" : ""}`;
$("btn-logout").addEventListener("click", () => window.AUTH.logout());

/* -------- tabs -------- */
const TABS = ["channels", "users", "history", "errors"];
document.querySelectorAll(".admin-tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".admin-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    TABS.forEach((t) => ($("tab-" + t).hidden = t !== b.dataset.tab));
    if (b.dataset.tab === "history" || b.dataset.tab === "errors") loadLogs();
    if (b.dataset.tab === "users") loadUsers();
  }));

/* -------- 데이터 로드 -------- */
async function bundle() {
  if (!BUNDLE) BUNDLE = await window.AUTH.loadBundle();
  return BUNDLE;
}

/* -------- channels -------- */
async function loadChannels() {
  const d = (await bundle()).channels || { channels: [] };
  channels = d.channels || [];
  channelsMeta = { _comment: d._comment || "제휴채널 마스터", updated_at: d.updated_at };
  renderChannels();
}

function renderChannels() {
  const tb = $("ch-body"); tb.innerHTML = "";
  channels.sort((a, b) => (a.sort ?? 999) - (b.sort ?? 999));
  channels.forEach((c, i) => {
    const tr = document.createElement("tr");
    tr.draggable = true; tr.dataset.i = i;
    tr.innerHTML = `
      <td class="drag-handle">⠿</td>
      <td><code>${esc(c.code)}</code></td>
      <td><input type="text" data-k="name" value="${esc(c.name)}" /></td>
      <td><input type="text" data-k="type" value="${esc(c.type || "")}" style="width:90px" /></td>
      <td><input type="checkbox" data-k="use_yn" ${c.use_yn === "Y" ? "checked" : ""} /></td>
      <td><input type="checkbox" data-k="display_yn" ${c.display_yn === "Y" ? "checked" : ""} /></td>
      <td><input type="text" data-k="sort" value="${c.sort ?? i + 1}" style="width:44px" /></td>
      <td class="row-actions"><button data-del="${i}">삭제</button></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input").forEach((inp) =>
    inp.addEventListener("change", () => {
      const tr = inp.closest("tr"); const c = channels[+tr.dataset.i]; const k = inp.dataset.k;
      if (inp.type === "checkbox") c[k] = inp.checked ? "Y" : "N";
      else if (k === "sort") c[k] = parseInt(inp.value, 10) || 0;
      else c[k] = inp.value;
    }));
  tb.querySelectorAll("[data-del]").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (confirm(`${channels[+btn.dataset.del].code} 채널을 삭제할까요?`)) {
        channels.splice(+btn.dataset.del, 1); renderChannels();
      }
    }));
  enableDrag(tb);
}

function enableDrag(tb) {
  let dragEl;
  tb.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("dragstart", () => { dragEl = tr; tr.classList.add("dragging"); });
    tr.addEventListener("dragend", () => {
      tr.classList.remove("dragging");
      [...tb.querySelectorAll("tr")].forEach((r, idx) => (channels[+r.dataset.i].sort = idx + 1));
      renderChannels();
    });
    tr.addEventListener("dragover", (e) => {
      e.preventDefault();
      const rows = [...tb.querySelectorAll("tr:not(.dragging)")];
      const after = rows.find((r) => e.clientY <= r.getBoundingClientRect().top + r.offsetHeight / 2);
      if (after) tb.insertBefore(dragEl, after); else tb.appendChild(dragEl);
    });
  });
}

$("ch-add").addEventListener("click", () => {
  const code = $("new-code").value.trim();
  if (!code) return;
  if (channels.some((c) => c.code === code)) return banner("warn", "이미 있는 code 입니다.");
  channels.push({ code, name: code, type: "기타", use_yn: "Y", display_yn: "Y", sort: channels.length + 1 });
  $("new-code").value = ""; renderChannels();
});

$("ch-download").addEventListener("click", () => {
  const out = Object.assign({}, channelsMeta, { updated_at: new Date().toISOString(), channels });
  download("channels.json", JSON.stringify(out, null, 2));
  banner("ok", "channels.json 다운로드. data/live/channels.json 에 넣고 publish.bat 실행하세요.");
});

/* -------- users -------- */
async function loadUsers() {
  if (!me || me.role !== "admin") {
    $("u-body").innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--ink-3)">관리자만 사용할 수 있습니다.</td></tr>`;
    return;
  }
  if (!usersDoc) usersDoc = await fetch("users.json?_=" + Date.now()).then((r) => r.json());
  renderUsers();
}

function renderUsers() {
  const tb = $("u-body"); tb.innerHTML = "";
  const users = usersDoc.users || {};
  Object.keys(users).forEach((uid) => {
    const u = users[uid];
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><code>${esc(uid)}</code></td><td>${esc(u.name || "")}</td>
      <td>${u.role === "admin" ? "관리자" : "조회"}</td>
      <td class="row-actions">${uid === me.id ? "<span style='color:var(--ink-3)'>(본인)</span>"
        : `<button data-del="${esc(uid)}">제거</button>`}</td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("[data-del]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const uid = btn.dataset.del;
      if (confirm(`'${uid}' 사용자를 제거할까요?`)) { delete usersDoc.users[uid]; renderUsers(); }
    }));
}

$("u-add").addEventListener("click", async () => {
  const id = $("u-id").value.trim(), name = $("u-name").value.trim(), pw = $("u-pw").value;
  if (!/^[A-Za-z0-9_.-]{2,}$/.test(id)) return banner("warn", "아이디는 영문/숫자 2자 이상.");
  if (!name) return banner("warn", "이름을 입력하세요.");
  if (pw.length < 6) return banner("warn", "임시 비밀번호는 6자 이상.");
  if (usersDoc.users[id]) return banner("warn", "이미 있는 아이디입니다.");
  try {
    const wrapped = await window.AUTH.wrapForNewUser(pw, usersDoc.iterations);
    usersDoc.users[id] = { name, role: $("u-admin").checked ? "admin" : "viewer", ...wrapped };
    $("u-id").value = $("u-name").value = $("u-pw").value = ""; $("u-admin").checked = false;
    renderUsers();
    banner("ok", `'${id}' 추가됨. 목록 저장은 아래 "users.json 다운로드".`);
  } catch (e) { banner("err", "추가 실패: " + e.message, true); }
});

$("u-download").addEventListener("click", () => {
  download("users.json", JSON.stringify(usersDoc, null, 2));
  banner("ok", "users.json 다운로드. docs/users.json 에 커밋하면 반영됩니다.");
});

/* -------- logs -------- */
async function loadLogs() {
  const d = (await bundle()).log || { logs: [] };
  const logs = (d.logs || []).slice().reverse();
  const badge = (s) => ({
    success: '<span class="delta up">정상</span>', skipped: '<span class="delta flat">중복</span>',
    error: '<span class="delta down">오류</span>',
  }[s] || s);
  const dsrc = (s) => s === "subject" ? '<span class="badge-type">제목 날짜</span>'
    : s === "received-1d" ? '<span class="badge-type" style="color:var(--warn);border-color:#f0d9a8">수신일-1일</span>' : "";
  $("hist-body").innerHTML = logs.map((l) => `<tr>
    <td>${l.collected_at ? new Date(l.collected_at).toLocaleString("ko-KR") : "-"}</td>
    <td>${l.record_date || "-"} ${dsrc(l.date_source)}</td><td>${badge(l.status)}</td>
    <td style="text-align:left;white-space:normal">${esc(l.subject)}</td>
    <td style="text-align:left">${esc(l.sender)}</td>
    <td style="text-align:left;white-space:normal">${esc(l.message)}</td></tr>`).join("")
    || `<tr><td colspan="6" style="text-align:center;color:var(--ink-3)">수집이력 없음</td></tr>`;
  const probs = logs.filter((l) => l.status === "error" || (l.warnings && l.warnings.length));
  $("err-body").innerHTML = probs.length ? probs.map((l) => `<tr>
    <td>${l.collected_at ? new Date(l.collected_at).toLocaleString("ko-KR") : "-"}</td>
    <td>${l.record_date || "-"}</td><td>${badge(l.status)}</td>
    <td style="text-align:left;white-space:normal">${esc(l.message)}${
      (l.warnings || []).map((w) => `<br><span style="color:var(--warn)">⚠ ${esc(w)}</span>`).join("")}</td></tr>`).join("")
    : `<tr><td colspan="4" style="text-align:center;color:var(--ink-3)">오류/경고 없음</td></tr>`;
}

/* -------- util -------- */
function download(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  a.download = name; a.click(); URL.revokeObjectURL(a.href);
}

if (me && me.role === "admin") $("tabbtn-users").hidden = false;
loadChannels().catch((e) => banner("err", "데이터 로드 실패: " + e.message, true));
